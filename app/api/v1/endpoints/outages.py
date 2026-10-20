from __future__ import annotations

from typing import Any, Dict, List, Optional
import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import BulkOutageCreate, Outage, OutageCreate, OutageUpdate
from app.models.enums import OutageStatus, Severity
from app.models.outage import (
    BulkResolveFailure,
    BulkResolveOutageRequest,
    BulkResolveOutageResponse,
    PaginatedOutages,
    ResolveOutageRequest,
)
from app.models.outage_dto import (
    OutageSortDirection,
    OutageSortField,
    ImportConsistency,
    ImportFieldError,
    ImportRowResult,
    ImportResponse,
)
from app.models.webhook import WebhookEvent
from app.repositories.outage_event_repository import OutageEventRepository
from app.repositories.outage_repository import OutageRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.sla_repository import SLARepository
from app.services.outage_store import outage_store
from app.core.outage_state_machine import InvalidStateTransition, OutageStateMachine
from app.services.audit_log import audit_log
from app.services.contracts import SLAContractAdapter, translate_contract_result
from app.services.webhook_service import trigger_sla_violation_webhooks
from app.utils.exporter import (
    export_outages,
    resolve_export_columns,
    stream_outages_csv,
)
from app.api.v1.endpoints.sla import _invalidate_analytics_cache
from app.core.security import require_engineer, require_admin
from app.core.config import settings
from app.core.lock import advisory_lock_nowait, ConcurrencyLockError
from app.models.sla import SLAResult
from app.models.payment import PaymentTransaction

router = APIRouter()


# --------------------------------------------------------------------------- #
# Response schemas (Issue #502: explicit OpenAPI response models)             #
# --------------------------------------------------------------------------- #

class OutageViolation(BaseModel):
    """An outage paired with its computed SLA result (violations view)."""
    outage: Outage
    sla: SLAResult


class DeleteOutageResponse(BaseModel):
    message: str = Field(..., description="Human-readable confirmation message")
    deleted_at: datetime | None = Field(None, description="Timestamp the outage was soft-deleted")


class RestoreOutageResponse(BaseModel):
    message: str = Field(..., description="Human-readable confirmation message")
    outage: Outage = Field(..., description="The restored outage record")


class ResolveOutageResponse(BaseModel):
    outage: Outage = Field(..., description="The resolved outage record")
    sla: SLAResult = Field(..., description="Computed SLA result for the outage")
    payment: PaymentTransaction = Field(..., description="Payment transaction created from the SLA result")
    resolution_notes: Optional[str] = Field(
        None,
        description="Operator resolution notes captured with this resolve (if provided)",
    )


class RecomputeSlaResponse(BaseModel):
    sla: SLAResult = Field(..., description="Recomputed SLA result for the outage")
    payment: PaymentTransaction = Field(..., description="Payment transaction created from the SLA result")


class PaginatedOutageEvents(BaseModel):
    items: List[Dict[str, Any]] = Field(..., description="Event timeline entries for the outage")
    total: int = Field(..., description="Total number of matching events")
    page: int = Field(..., ge=1, description="Current page number (1-indexed)")
    page_size: int = Field(..., ge=1, le=100, description="Number of events per page")


class ValidTransitionsResponse(BaseModel):
    outage_id: str = Field(..., description="Outage ID the transitions apply to")
    current_status: str = Field(..., description="Current outage status")
    valid_next_states: List[str] = Field(..., description="Sorted list of permitted next statuses")


class OutageCountResponse(BaseModel):
    """Count-only projection for the lightweight count endpoint (#964)."""

    count: int = Field(..., ge=0, description="Number of outages matching the supplied filters.")
