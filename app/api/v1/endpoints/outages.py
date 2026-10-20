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
