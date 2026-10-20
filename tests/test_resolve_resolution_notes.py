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
