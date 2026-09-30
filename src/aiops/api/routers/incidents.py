"""Incident 관리 API (2.4)."""

from fastapi import APIRouter, HTTPException

from aiops.api.deps import SessionDep
from aiops.db.repositories import IncidentRepository
from aiops.domain.models import Incident, IncidentStatus

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
