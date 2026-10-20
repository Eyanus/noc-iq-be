"""Unit tests for optional resolution_notes on the single-outage resolve flow.

Acceptance criteria:
- Optional field accepted on ResolveOutageRequest
- Notes persisted with the resolved outage event
- Notes returned in the resolve response
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models.outage import Outage, ResolveOutageRequest
from app.models.outage_event import OutageResolvedDetail, validate_event_detail


class TestResolveOutageRequestNotes:
    def test_optional_field_accepted_when_provided(self):
        req = ResolveOutageRequest(mttr_minutes=30, resolution_notes="Fiber splice completed")
        assert req.mttr_minutes == 30
        assert req.resolution_notes == "Fiber splice completed"

    def test_optional_field_defaults_to_none(self):
        req = ResolveOutageRequest(mttr_minutes=15)
        assert req.resolution_notes is None

    def test_empty_string_notes_accepted(self):
        req = ResolveOutageRequest(mttr_minutes=10, resolution_notes="")
        assert req.resolution_notes == ""


class TestOutageResolvedDetailNotes:
    def test_event_detail_includes_notes_when_provided(self):
        detail = validate_event_detail(
            "resolved",
            {"mttr_minutes": 45, "resolution_notes": "Replaced ONT"},
        )
        assert detail["mttr_minutes"] == 45
        assert detail["resolution_notes"] == "Replaced ONT"

    def test_event_detail_omits_notes_when_absent(self):
        detail = validate_event_detail("resolved", {"mttr_minutes": 20})
        assert detail["mttr_minutes"] == 20
        assert detail.get("resolution_notes") is None


class TestResolveEndpointNotesPersistence:
    """Exercise the resolve endpoint with mocked dependencies."""

    def setup_method(self):
        self.client = TestClient(app)
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = True

        def _get_db():
            yield mock_db

        from app.core.security import require_engineer

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[require_engineer] = lambda: {
            "user_id": "eng-1",
            "role": "engineer",
        }
        self.mock_db = mock_db

    def teardown_method(self):
        app.dependency_overrides.clear()

    def _resolved_outage(self):
        return Outage(
            id="out_1",
            site_name="Site A",
            severity="high",
            status="resolved",
            detected_at=datetime(2023, 10, 1, 12, 0, 0, tzinfo=timezone.utc),
            resolved_at=datetime(2023, 10, 1, 12, 30, 0, tzinfo=timezone.utc),
            description="Fiber cut",
            affected_services=["4G"],
        )

    def test_resolve_returns_and_persists_resolution_notes(self):
        outage = self._resolved_outage()
        notes = "Operator: splice completed at splice box 12"

        recorded = []

        class FakeRepo:
            def get(self, outage_id):
                o = outage.model_copy()
                o.status = "open"
                return o

            def resolve(self, outage_id, mttr_minutes):
                return outage

        class FakeEventRepo:
            def __init__(self, db):
                pass

            def record(self, outage_id, event_type, detail=None):
                recorded.append({"outage_id": outage_id, "event_type": event_type, "detail": detail})
                return SimpleNamespace(id="evt_1")

        sla = SimpleNamespace(
            status="met",
            mttr_minutes=30,
            threshold_minutes=60,
            amount=0.0,
            payment_type="reward",
            rating="good",
            model_dump=lambda: {"status": "met", "mttr_minutes": 30},
        )
        payment = SimpleNamespace(model_dump=lambda: {"id": "pay_1"})

        with (
            patch("app.api.v1.endpoints.outages.OutageRepository", return_value=FakeRepo()),
            patch("app.api.v1.endpoints.outages.OutageEventRepository", FakeEventRepo),
            patch("app.api.v1.endpoints.outages.OutageStateMachine") as sm,
            patch("app.api.v1.endpoints.outages.advisory_lock_nowait") as lock,
            patch("app.api.v1.endpoints.outages.SLAContractAdapter") as adapter,
            patch("app.api.v1.endpoints.outages.translate_contract_result", return_value=sla),
            patch("app.api.v1.endpoints.outages.SLARepository") as sla_repo_cls,
            patch("app.api.v1.endpoints.outages.PaymentRepository") as pay_repo_cls,
            patch("app.api.v1.endpoints.outages.trigger_sla_violation_webhooks"),
            patch("app.api.v1.endpoints.outages._invalidate_analytics_cache"),
            patch("app.api.v1.endpoints.outages.audit_log"),
        ):
            lock.return_value.__enter__ = MagicMock(return_value=None)
            lock.return_value.__exit__ = MagicMock(return_value=False)
            sm.transition.return_value = None
            adapter.calculate_sla.return_value = {"status": "met"}
            sla_repo_cls.return_value.create_if_changed.return_value = sla
            pay_repo_cls.return_value.create_for_sla_result.return_value = payment

            response = self.client.post(
                "/api/v1/outages/out_1/resolve",
                json={"mttr_minutes": 30, "resolution_notes": notes},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body.get("resolution_notes") == notes

        resolved_events = [e for e in recorded if e["event_type"] == "resolved"]
        assert len(resolved_events) == 1
        assert resolved_events[0]["detail"]["mttr_minutes"] == 30
        assert resolved_events[0]["detail"]["resolution_notes"] == notes

    def test_resolve_without_notes_still_succeeds(self):
        outage = self._resolved_outage()
        recorded = []

        class FakeRepo:
            def get(self, outage_id):
                o = outage.model_copy()
                o.status = "open"
                return o

            def resolve(self, outage_id, mttr_minutes):
                return outage

        class FakeEventRepo:
            def __init__(self, db):
                pass

            def record(self, outage_id, event_type, detail=None):
                recorded.append({"event_type": event_type, "detail": detail})
                return SimpleNamespace(id="evt_1")

        sla = SimpleNamespace(
            status="met",
            mttr_minutes=20,
            model_dump=lambda: {"status": "met"},
        )
        payment = SimpleNamespace(model_dump=lambda: {})

        with (
            patch("app.api.v1.endpoints.outages.OutageRepository", return_value=FakeRepo()),
            patch("app.api.v1.endpoints.outages.OutageEventRepository", FakeEventRepo),
            patch("app.api.v1.endpoints.outages.OutageStateMachine") as sm,
            patch("app.api.v1.endpoints.outages.advisory_lock_nowait") as lock,
            patch("app.api.v1.endpoints.outages.SLAContractAdapter") as adapter,
            patch("app.api.v1.endpoints.outages.translate_contract_result", return_value=sla),
            patch("app.api.v1.endpoints.outages.SLARepository") as sla_repo_cls,
            patch("app.api.v1.endpoints.outages.PaymentRepository") as pay_repo_cls,
            patch("app.api.v1.endpoints.outages.trigger_sla_violation_webhooks"),
            patch("app.api.v1.endpoints.outages._invalidate_analytics_cache"),
            patch("app.api.v1.endpoints.outages.audit_log"),
        ):
            lock.return_value.__enter__ = MagicMock(return_value=None)
            lock.return_value.__exit__ = MagicMock(return_value=False)
            sm.transition.return_value = None
            adapter.calculate_sla.return_value = {}
            sla_repo_cls.return_value.create_if_changed.return_value = sla
            pay_repo_cls.return_value.create_for_sla_result.return_value = payment

            response = self.client.post(
                "/api/v1/outages/out_1/resolve",
                json={"mttr_minutes": 20},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body.get("resolution_notes") is None
        resolved_events = [e for e in recorded if e["event_type"] == "resolved"]
        assert len(resolved_events) == 1
        assert "resolution_notes" not in resolved_events[0]["detail"]
