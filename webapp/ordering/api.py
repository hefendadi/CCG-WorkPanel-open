"""Authenticated Ordering Workbench API; no import or MDM writes."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from webapp.account_permissions import (
    EDIT,
    MODULE_ORDERING,
    VIEW,
    require_page_permission,
    require_permission,
)
from webapp.auth.dependencies import current_user
from webapp.mdm.database import get_session_factory
from .models import (PlanningCycle, CycleSourceBinding, DatasetVersion, IncomingSnapshot, ForecastVersion)
from .forecast_version import ForecastLineInput, ForecastVersionError
from .workbench import read_workbench, save_forecast, cycle_context, WorkbenchError

router = APIRouter(prefix='/api/v1/ordering', tags=['ordering'])
pages = APIRouter()
ordering_viewer = require_permission(MODULE_ORDERING, VIEW, current_user)
ordering_editor = require_permission(MODULE_ORDERING, EDIT, current_user)
ordering_page_viewer = require_page_permission(MODULE_ORDERING, VIEW)


@pages.get('/ordering')
@pages.get('/ordering/cycles/{cycle_id}')
def workbench_page(
    cycle_id: Optional[int] = None,
    user=Depends(ordering_page_viewer),
):
    return FileResponse(Path(__file__).resolve().parents[1] / 'static' / 'ordering.html',
                        headers={'Cache-Control': 'no-store'})


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CycleCreate(Payload):
    cycle_month: date
    name: str = Field(default='', max_length=40)


class Binding(Payload):
    dataset_version_id: int = Field(gt=0)
    period_start: date
    period_end: date


class Selection(Payload):
    bindings: list[Binding]
    incoming_snapshot_id: Optional[int] = None
    forecast_version_id: Optional[int] = None


class ForecastCell(Payload):
    channel_stable_id: str
    product_stable_id: str
    forecast_month: date
    forecast_qty: Decimal


class ForecastSave(Payload):
    expected_version_id: Optional[int] = None
    lines: list[ForecastCell]


def actor(user):
    return str(user.get('username') or user['id'])


@router.get('/cycles')
def cycles(user=Depends(ordering_viewer)):
    with get_session_factory()() as s:
        return [dict(id=c.id, name=c.cycle_code, month=c.cycle_month, status=c.status.value)
                for c in s.scalars(select(PlanningCycle).order_by(PlanningCycle.id.desc()))]


@router.post('/cycles', status_code=201)
def create_cycle(body: CycleCreate, user=Depends(ordering_editor)):
    if body.cycle_month.day != 1:
        raise HTTPException(422, 'cycle_month must be a month start')
    with get_session_factory()() as s, s.begin():
        c = PlanningCycle(cycle_code=f'{body.name.strip() or body.cycle_month.strftime("%Y-%m")} {uuid4().hex[:8]}',
                          cycle_month=body.cycle_month, created_by=actor(user))
        s.add(c)
        s.flush()
        return dict(id=c.id)


@router.get('/cycles/{cycle_id}')
def workbench(cycle_id: int, user=Depends(ordering_viewer)):
    try:
        return read_workbench(get_session_factory(), cycle_id)
    except WorkbenchError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/cycles/{cycle_id}/source-options')
def source_options(cycle_id: int, user=Depends(ordering_viewer)):
    with get_session_factory()() as s, s.begin():
        try:
            cycle_context(s, cycle_id, writing=True)
        except WorkbenchError as exc:
            raise HTTPException(409, str(exc)) from exc
        return dict(datasets=[dict(id=v.id, type=v.dataset_type.value, label=v.version_label,
                                   name=v.source_name, start=v.period_start, end=v.period_end)
                              for v in s.scalars(select(DatasetVersion).order_by(DatasetVersion.id.desc()))],
                    incoming=[dict(id=v.id, name=v.snapshot_key, at=v.snapshot_at)
                              for v in s.scalars(select(IncomingSnapshot).order_by(IncomingSnapshot.id.desc()))],
                    forecasts=[dict(id=v.id, number=v.version_no)
                               for v in s.scalars(select(ForecastVersion).where(ForecastVersion.cycle_id == cycle_id)
                                                  .order_by(ForecastVersion.version_no.desc()))])


@router.put('/cycles/{cycle_id}/sources')
def choose_sources(cycle_id: int, body: Selection, user=Depends(ordering_editor)):
    try:
        with get_session_factory()() as s, s.begin():
            c = cycle_context(s, cycle_id, writing=True)
            periods = {}
            for b in body.bindings:
                v = s.get(DatasetVersion, b.dataset_version_id)
                if (v is None or b.period_start.day != 1 or b.period_end.day != 1 or
                        not v.period_start <= b.period_start <= b.period_end <= v.period_end):
                    raise WorkbenchError('Binding period must be inside the selected version coverage')
                for start, end in periods.setdefault(v.dataset_type, []):
                    if b.period_start <= end and start <= b.period_end:
                        raise WorkbenchError('Overlapping source months: select only one version per type/month')
                periods[v.dataset_type].append((b.period_start, b.period_end))
            if body.incoming_snapshot_id is not None and s.get(IncomingSnapshot, body.incoming_snapshot_id) is None:
                raise WorkbenchError('Incoming snapshot not found')
            if body.forecast_version_id is not None:
                v = s.get(ForecastVersion, body.forecast_version_id)
                if v is None or v.cycle_id != c.id:
                    raise WorkbenchError('Forecast must belong to this cycle')
            for b in s.scalars(select(CycleSourceBinding).where(CycleSourceBinding.cycle_id == c.id)):
                s.delete(b)
            s.flush()
            s.add_all([CycleSourceBinding(cycle_id=c.id, **b.model_dump(), created_by=actor(user)) for b in body.bindings])
            c.incoming_snapshot_id = body.incoming_snapshot_id
            c.forecast_version_id = body.forecast_version_id
        return dict(saved=True)
    except (WorkbenchError, IntegrityError) as exc:
        raise HTTPException(409, str(exc) if isinstance(exc, WorkbenchError) else 'Source selection conflict') from exc


@router.post('/cycles/{cycle_id}/forecast', status_code=201)
def save(cycle_id: int, body: ForecastSave, user=Depends(ordering_editor)):
    try:
        result = save_forecast(get_session_factory(), cycle_id, body.expected_version_id,
                               [ForecastLineInput(**l.model_dump()) for l in body.lines], actor(user))
        return dict(id=result.forecast_version_id, number=result.version_no)
    except (WorkbenchError, ForecastVersionError) as exc:
        raise HTTPException(409, str(exc)) from exc
