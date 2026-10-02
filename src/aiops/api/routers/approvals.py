"""사람 승인 API (M3-05) — 웹 UI·CLI·Slack 버튼이 같은 결정 경로를 쓴다.

권한 (M4-01): 승인·거부는 approver 역할, 결정자는 요청 본문이 아니라 인증 주체다.
Slack 콜백은 API Key 대신 서명(HMAC) + 승인자 허용 목록으로 보호한다.
"""

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from aiops.api.auth import PUBLIC, PrincipalDep, Role, actor_of, requires
from aiops.api.deps import PlatformDep
from aiops.services.approvals import Approval, ApprovalConflict
from aiops.services.notify import SlackSignatureError, verify_slack_signature

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


class DecisionRequest(BaseModel):
    actor: str | None = None  # 인증이 꺼졌을 때만 사용 (켜져 있으면 인증 주체로 대체)
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
@requires(Role.APPROVER)
async def approve(
    approval_id: str,
    req: DecisionRequest,
    p: PlatformDep,
    who: PrincipalDep,
    background: BackgroundTasks,
) -> Approval:
    return await _decide(p, approval_id, True, actor_of(who, req.actor), req.reason, background)


@router.post("/{approval_id}/reject", response_model=Approval)
@requires(Role.APPROVER)
async def reject(
    approval_id: str,
    req: DecisionRequest,
    p: PlatformDep,
    who: PrincipalDep,
    background: BackgroundTasks,
) -> Approval:
    return await _decide(p, approval_id, False, actor_of(who, req.actor), req.reason, background)


@router.post("/slack/actions")
@requires(PUBLIC)  # API Key 대신 Slack 서명으로 인증
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
    allowed = p.settings.slack_approvers
    # 인증을 켰다면 허용 목록이 비어 있을 때 아무도 승인할 수 없다 (fail-closed)
    if (allowed or p.settings.auth_mode != "off") and not (
        {user.get("id"), user.get("username")} & allowed
    ):
        return {"response_type": "ephemeral", "text": f"{actor} 는 승인 권한이 없습니다"}
    action = payload["actions"][0]
    a = await _decide(
        p, action["value"], action["action_id"] == "approve", actor, "Slack 버튼", background
    )
    return {"text": f"{a.status} by {actor}"}


@router.post("/sweep")
@requires(Role.ADMIN)
async def sweep(p: PlatformDep) -> dict:
    """미응답 에스컬레이션·만료 자동 거부 (주기 스윕을 끈 경우 외부 cron 용)."""
    return await p.approvals.sweep()
