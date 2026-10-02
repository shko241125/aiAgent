"""Incident 관리 API (2.4) — 상태 머신·타임라인 (M3-04)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aiops.api.deps import PlatformDep, SessionDep
from aiops.db.repositories import IncidentRepository
from aiops.domain.models import Incident, IncidentStatus
from aiops.rag.knowledge import postmortem_document
from aiops.rag.service import IngestReport
from aiops.services.incidents import InvalidTransition, TimelineEvent

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


@router.get("", response_model=list[Incident])
async def list_incidents(
    session: SessionDep,
    status: IncidentStatus | None = None,
    limit: int = 50,
) -> list[Incident]:
    return await IncidentRepository(session).list(status=status, limit=limit)


@router.get("/{incident_id}", response_model=Incident)
async def get_incident(incident_id: str, session: SessionDep) -> Incident:
    inc = await IncidentRepository(session).get(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    return inc


@router.post("", response_model=Incident, status_code=201)
async def create_incident(incident: Incident, session: SessionDep) -> Incident:
    return await IncidentRepository(session).create(incident)


class ResolveRequest(BaseModel):
    resolution: str
    actor: str = "human"
    root_cause: str | None = None  # 사람이 확정한 원인 (에이전트 추정을 덮어씀)
    actions: list[str] = []


class ResolveResponse(BaseModel):
    incident: Incident
    knowledge_doc_id: str
    ingest: IngestReport


@router.post("/{incident_id}/resolve", response_model=ResolveResponse)
async def resolve_incident(
    incident_id: str, req: ResolveRequest, session: SessionDep, p: PlatformDep
) -> ResolveResponse:
    """인시던트 해결 + 포스트모템 지식화 (M1-09): RAG 에 즉시 반영되고 DB 에 영속화된다."""
    repo = IncidentRepository(session)
    inc = await repo.get(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    try:
        await p.incidents.transition(
            incident_id, IncidentStatus.RESOLVED, req.actor, req.resolution
        )
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    fields: dict = {}
    if req.root_cause:
        fields["root_cause"] = req.root_cause
    if req.actions:
        fields["actions"] = [*inc.actions, *req.actions]
    if fields:
        await repo.update(incident_id, **fields)
    # 전이는 다른 세션에서 일어났다 → 요청 세션의 캐시가 아니라 새로 읽은 값을 쓴다
    inc = await p.incidents.get(incident_id)
    doc = postmortem_document(inc, req.resolution)
    await p.knowledge.upsert(doc)
    report = await p.rag.ingest([doc])
    return ResolveResponse(incident=inc, knowledge_doc_id=doc.id, ingest=report)


class TransitionRequest(BaseModel):
    to: IncidentStatus
    actor: str = "human"
    note: str = ""


class NoteRequest(BaseModel):
    actor: str = "human"
    message: str


@router.get("/{incident_id}/timeline")
async def timeline(incident_id: str, p: PlatformDep) -> dict:
    """인시던트 타임라인 (M3-04) + MTTR(해결까지 걸린 시간)."""
    events = await p.incidents.timeline(incident_id)
    return {
        "events": [e.model_dump(mode="json") for e in events],
        "mttr_minutes": p.incidents.mttr_minutes(events),
    }


@router.post("/{incident_id}/transition", response_model=Incident)
async def transition(incident_id: str, req: TransitionRequest, p: PlatformDep) -> Incident:
    try:
        return await p.incidents.transition(incident_id, req.to, req.actor, req.note)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{incident_id}/notes", response_model=TimelineEvent)
async def note(incident_id: str, req: NoteRequest, p: PlatformDep) -> TimelineEvent:
    return await p.incidents.record(incident_id, "note", req.actor, req.message)
