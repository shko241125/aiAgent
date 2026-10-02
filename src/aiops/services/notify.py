"""알림 채널 (M3-05 / 2.3) — 승인 요청·에스컬레이션을 사람에게 전달.

- LogNotifier          : 기본값 (로그 + 메모리 기록, 테스트용)
- SlackWebhookNotifier : Slack Incoming Webhook + Block Kit 버튼(승인/거부)
  버튼을 누르면 Slack 이 interactivity URL(/api/v1/approvals/slack/actions)로 콜백한다.
"""

import hashlib
import hmac
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class Notifier(ABC):
    @abstractmethod
    async def approval_requested(self, approval: dict[str, Any]) -> None: ...

    @abstractmethod
    async def escalated(self, approval: dict[str, Any]) -> None: ...

    @abstractmethod
    async def info(self, text: str) -> None: ...


class LogNotifier(Notifier):
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def approval_requested(self, approval: dict[str, Any]) -> None:
        self.sent.append(("requested", approval["id"]))
        logger.info("승인 요청 %s: %s", approval["id"], approval.get("summary"))

    async def escalated(self, approval: dict[str, Any]) -> None:
        self.sent.append(("escalated", approval["id"]))
        logger.warning("승인 에스컬레이션 %s", approval["id"])

    async def info(self, text: str) -> None:
        self.sent.append(("info", text))
        logger.info(text)


class SlackWebhookNotifier(Notifier):
    def __init__(
        self,
        webhook_url: str,
        escalation_mention: str = "<!here>",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.mention = escalation_mention
        self._client = http_client or httpx.AsyncClient(timeout=10)

    async def _post(self, body: dict[str, Any]) -> None:
        try:
            resp = await self._client.post(self.webhook_url, json=body)
            if resp.status_code >= 400:
                logger.warning("Slack 전송 실패 %s: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:  # 알림 실패가 워크플로우를 멈추면 안 된다
            logger.warning("Slack 전송 오류: %s", exc)

    async def approval_requested(self, approval: dict[str, Any]) -> None:
        actions = approval.get("details", {}).get("actions", [])
        head = f"*조치 승인 요청* (`{approval['id']}`)\n{approval.get('summary', '')}"
        lines = "\n".join(
            f"• `{a.get('type')}` {a.get('namespace', '')}/{a.get('service')}"
            f" — {a.get('reason', '')}"
            for a in actions
        )
        text = f"{head}\n{lines}\n_만료: {approval.get('expires_at')}_"
        await self._post(
            {
                "text": f"[승인 요청] {approval.get('summary', '')}",  # 알림 미리보기용 대체 텍스트
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": text,
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "action_id": "approve",
                                "style": "primary",
                                "text": {"type": "plain_text", "text": "승인"},
                                "value": approval["id"],
                            },
                            {
                                "type": "button",
                                "action_id": "reject",
                                "style": "danger",
                                "text": {"type": "plain_text", "text": "거부"},
                                "value": approval["id"],
                            },
                        ],
                    },
                ],
            }
        )

    async def escalated(self, approval: dict[str, Any]) -> None:
        await self._post(
            {
                "text": f"{self.mention} 승인 대기 중인 조치가 응답이 없습니다: "
                f"`{approval['id']}` {approval.get('summary', '')}"
            }
        )

    async def info(self, text: str) -> None:
        await self._post({"text": text})


class SlackSignatureError(ValueError):
    pass


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    now: float | None = None,
    tolerance_s: int = 300,
) -> None:
    """Slack 요청 서명 검증: v0=HMAC_SHA256(secret, "v0:{timestamp}:{raw body}").

    - 타임스탬프가 5분 이상 차이 나면 거부 → 가로챈 요청을 나중에 다시 보내는 재생 공격 방지
    - compare_digest 로 비교 → 문자 단위 비교 시간 차이로 서명을 추측하는 타이밍 공격 방지
    """
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise SlackSignatureError("타임스탬프 없음") from exc
    if abs((now or time.time()) - ts) > tolerance_s:
        raise SlackSignatureError("타임스탬프 만료 (재생 공격 의심)")
    base = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise SlackSignatureError("서명 불일치")
