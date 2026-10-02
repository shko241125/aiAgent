"""사람 승인 API (M3-05) — 웹 UI·CLI·Slack 버튼이 같은 결정 경로를 쓴다.

TODO(4.4): 인증/인가 — 현재 actor 는 자기 신고. 운영 전 OIDC·역할 기반 승인 권한 필요.
"""

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from aiops.api.deps import PlatformDep
from aiops.services.approvals import Approval, ApprovalConflict
from aiops.services.notify import SlackSignatureError, verify_slack_signature

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


class DecisionRequest(BaseModel):
    actor: str
    reason: str = ""


@router.get("", response_model=list[Approval])
async def list_approvals(p: PlatformDep, status: str | None = "pending") -> list[Approval]:
    return await p.approvals.find(status)


@router.get("/{approval_id}", response_model=Approval)
async def get_approval(approval_id: str, p: PlatformDep) -> Approval:
    a = await p.approvals.get(approval_id)
    if a is None:
        raise HTTPException(404, "approval not found")
    return a


async def _decide(
    p, aid: str, approved: bool, actor: str, reason: str, background: BackgroundTasks
) -> Approval:
    try:
        a = await p.approvals.decide(aid, approved, actor, reason)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    background.add_task(p.approvals.after_decision, a)  # 재개는 응답 후 (Slack 3초 제한)
    return a


@router.post("/{approval_id}/approve", response_model=Approval)
async def approve(
    approval_id: str, req: DecisionRequest, p: PlatformDep, background: BackgroundTasks
) -> Approval:
    return await _decide(p, approval_id, True, req.actor, req.reason, background)


@router.post("/{approval_id}/reject", response_model=Approval)
async def reject(
    approval_id: str, req: DecisionRequest, p: PlatformDep, background: BackgroundTasks
) -> Approval:
    return await _decide(p, approval_id, False, req.actor, req.reason, background)


@router.post("/slack/actions")
async def slack_actions(request: Request, p: PlatformDep, background: BackgroundTasks) -> dict:
    """Slack interactivity 콜백 (버튼 클릭). 서명 검증 후 승인/거부."""
    secret = p.settings.slack_signing_secret
    if not secret:
        raise HTTPException(503, "slack_signing_secret 미설정")
    body = await request.body()
    try:
        verify_slack_signature(
            secret,
            request.headers.get("X-Slack-Request-Timestamp", ""),
            body,
            request.headers.get("X-Slack-Signature", ""),
        )
    except SlackSignatureError as exc:
        raise HTTPException(401, str(exc)) from exc
    payload = json.loads(parse_qs(body.decode())["payload"][0])
    user = payload.get("user", {})
    actor = f"slack:{user.get('username') or user.get('id', '?')}"
    action = payload["actions"][0]
    a = await _decide(
        p, action["value"], action["action_id"] == "approve", actor, "Slack 버튼", background
    )
    return {"text": f"{a.status} by {actor}"}


@router.post("/sweep")
async def sweep(p: PlatformDep) -> dict:
    """미응답 에스컬레이션·만료 자동 거부 (주기 스윕을 끈 경우 외부 cron 용)."""
    return await p.approvals.sweep()
