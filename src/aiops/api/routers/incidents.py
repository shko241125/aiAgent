"""Incident 관리 API (2.4)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aiops.api.deps import PlatformDep, SessionDep
from aiops.db.repositories import IncidentRepository
from aiops.domain.models import Incident, IncidentStatus
from aiops.rag.knowledge import postmortem_document
from aiops.rag.service import IngestReport

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
    """인시던트 종료 + 포스트모템 지식화 (M1-09): RAG 에 즉시 반영되고 DB 에 영속화된다."""
    repo = IncidentRepository(session)
    inc = await repo.get(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    fields = {"status": IncidentStatus.RESOLVED.value}
    if req.root_cause:
        fields["root_cause"] = req.root_cause
    if req.actions:
        fields["actions"] = [*inc.actions, *req.actions]
    inc = await repo.update(incident_id, **fields)
    doc = postmortem_document(inc, req.resolution)
    await p.knowledge.upsert(doc)
    report = await p.rag.ingest([doc])
    return ResolveResponse(incident=inc, knowledge_doc_id=doc.id, ingest=report)
