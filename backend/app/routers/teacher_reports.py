"""Teacher routes for citation reports and model-written reports."""

import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.auth import require_teacher
from backend.app.database import get_db
from backend.app.models import AuditReport, Session, User
from backend.app.routers.teacher_access import _verify_class_ownership
from backend.app.schemas import ReportRequest
from backend.app.services import lab_service
from backend.app.services.citation_report import class_citation_report, lab_citation_report

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


# ===================================================================
# GET /api/teacher/labs/{lab_id}/citation-report
# ===================================================================


@router.get("/labs/{lab_id}/citation-report")
async def get_citation_report(
    lab_id: uuid.UUID,
    start: date | None = Query(None, description="First day included (UTC)."),
    end: date | None = Query(None, description="Last day included (UTC)."),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Which course files, pages and passages the lab's research answers cite,
    with the students and their questions (audit; ownership enforced).
    Shape: backend/app/services/citation_report.py."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    return await lab_citation_report(db, lab, **_report_period(start, end))


@router.get("/classes/{class_id}/citation-report")
async def get_class_citation_report(
    class_id: uuid.UUID,
    start: date | None = Query(None, description="First day included (UTC)."),
    end: date | None = Query(None, description="Last day included (UTC)."),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """The lab report for all labs of the class together; each question names its lab."""
    await _verify_class_ownership(db, class_id, teacher)
    labs = await lab_service.list_labs_for_class(db, class_id)
    return await class_citation_report(db, class_id, labs, **_report_period(start, end))


def _report_period(start: date | None, end: date | None) -> dict:
    """[start, end] in days (UTC) as the report's [start, end) datetimes."""
    if start and end and end < start:
        raise HTTPException(status_code=422, detail="end is before start")
    day = lambda d: datetime.combine(d, time.min, tzinfo=timezone.utc)
    return {'start': day(start) if start else None, 'end': day(end) + timedelta(days=1) if end else None}


# ===================================================================
# Reports a model writes for the teacher (a lab, or one student in it)
# ===================================================================


async def _report_lab(db, lab_id, teacher, student_id):
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    if student_id is not None and not await db.scalar(select(Session.id).where(
            Session.lab_id == lab.id, Session.user_id == student_id).limit(1)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No conversation of this student in this lab")
    return lab


async def _latest_report(db, lab_id, student_id):
    return await db.scalar(select(AuditReport).where(
        AuditReport.lab_id == lab_id,
        AuditReport.student_id == student_id if student_id else AuditReport.student_id.is_(None))
        .order_by(AuditReport.created_at.desc()).limit(1))


@router.post("/labs/{lab_id}/reports", status_code=202)
async def create_report(
    lab_id: uuid.UUID,
    body: ReportRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Start a report on the lab, or on one student with `student_id`. The
    model writes it in the background (tens of seconds); poll the GET."""
    from backend.app.agent.model import ResearchModel
    from backend.app.services import teaching_reports
    from backend.app.services.model_clients import get_llm_config, make_embed_fn
    from backend.app.services.model_routing import Endpoint

    lab = await _report_lab(db, lab_id, teacher, body.student_id)
    latest = await _latest_report(db, lab.id, body.student_id)
    if latest is not None and latest.status == "running" and not teaching_reports.stale(latest):
        return teaching_reports.public(latest)          # one at a time
    config = await get_llm_config(db)
    agent = Endpoint(config["agent_base_url"], config["agent_api_key"], config["agent_model"])
    if not agent.model or not agent.base_url:
        raise HTTPException(503, "agent_model_not_configured")
    report = AuditReport(lab_id=lab.id, student_id=body.student_id, created_by=teacher.id)
    db.add(report)
    await db.commit()
    await db.refresh(report)
    teaching_reports.start_report(
        async_sessionmaker(db.bind, expire_on_commit=False), report.id, ResearchModel(agent),
        model_name=agent.model,
        embed_fn=make_embed_fn(config) if config.get("embedding_model") else None)
    return teaching_reports.public(report)


@router.get("/labs/{lab_id}/reports")
async def get_report(
    lab_id: uuid.UUID,
    student_id: uuid.UUID | None = Query(None),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """The latest report on the lab (or on one student), or `{"report": null}`."""
    from backend.app.services import teaching_reports
    lab = await _report_lab(db, lab_id, teacher, student_id)
    return {"report": teaching_reports.public(await _latest_report(db, lab.id, student_id))}
