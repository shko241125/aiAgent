"""API 인증·인가 (M4-01 / 4.4).

- 인증: API Key (`Authorization: Bearer <key>` 또는 `X-API-Key`). 설정에는 **SHA-256 해시만** 둔다.
- 인가: 역할 4종. viewer(조회) · operator(실행·기록) · approver(조치 승인) · admin(전부).
  라우트마다 필요한 역할은 `@requires(...)` 로 선언하고, 선언이 없으면 메서드로 정한다
  (GET → viewer, 그 외 → operator). 검사는 앱 전역 의존성 하나(`authorize`)가 한다.
- 행위자(actor): 인증이 켜져 있으면 요청 본문의 actor 는 무시하고 **인증 주체 이름**을 쓴다.

OIDC(JWT) 는 같은 `Principal` 을 돌려주는 인증기를 추가하면 된다. TODO(4.4): OIDC 인증기.
"""

import hashlib
import hmac
import json
from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from aiops.core.config import Settings


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMIN = "admin"


PUBLIC = "public"  # 인증 불필요 (헬스체크·메트릭·서명으로 보호되는 Slack 콜백)


class Principal(BaseModel):
    name: str
    roles: frozenset[str]
    authenticated: bool = True

    def has(self, role: str) -> bool:
        return role == Role.VIEWER or role in self.roles or Role.ADMIN in self.roles


ANONYMOUS = Principal(name="anonymous", roles=frozenset({Role.ADMIN}), authenticated=False)


class ApiKey(BaseModel):
    name: str
    sha256: str
    roles: list[Role]


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiKeyAuthenticator:
    def __init__(self, keys: list[ApiKey]) -> None:
        self.keys = keys

    def authenticate(self, token: str) -> Principal | None:
        digest = hash_key(token)
        found = None
        for k in self.keys:  # 일치해도 끝까지 비교 → 키 위치에 따른 응답 시간 차이를 줄인다
            if hmac.compare_digest(digest, k.sha256.lower()):
                found = k
        return Principal(name=found.name, roles=frozenset(found.roles)) if found else None


class InsecureConfig(RuntimeError):
    pass


def parse_api_keys(raw: str) -> list[ApiKey]:
    return [ApiKey.model_validate(k) for k in json.loads(raw or "[]")]


def build_authenticator(settings: Settings) -> ApiKeyAuthenticator | None:
    """auth_mode=off 면 None. prod 에서 인증이 꺼져 있거나 키가 없으면 기동을 거부한다."""
    keys = parse_api_keys(settings.api_keys)
    if settings.env == "prod":
        if settings.auth_mode == "off":
            raise InsecureConfig("prod 에서 AIOPS_AUTH_MODE=off 는 허용되지 않습니다")
        if not keys:
            raise InsecureConfig("prod 에서 AIOPS_API_KEYS 가 비어 있습니다")
    if settings.auth_mode == "off":
        return None
    if not keys:
        raise InsecureConfig("AIOPS_AUTH_MODE=api_key 인데 AIOPS_API_KEYS 가 비어 있습니다")
    return ApiKeyAuthenticator(keys)


def requires(role: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """라우트 함수에 필요한 역할을 표시한다 (메서드 기본값을 덮어씀)."""

    def mark(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn.__required_role__ = role
        return fn

    return mark


def _token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-api-key")


def required_role(request: Request) -> str:
    endpoint = request.scope.get("endpoint")
    declared = getattr(endpoint, "__required_role__", None)
    if declared:
        return declared
    return Role.VIEWER if request.method in ("GET", "HEAD", "OPTIONS") else Role.OPERATOR


async def authorize(request: Request) -> Principal:
    """앱 전역 의존성: 인증 → 역할 검사 → request.state.principal 저장."""
    role = required_role(request)
    auth: ApiKeyAuthenticator | None = request.app.state.authenticator
    if auth is None or role == PUBLIC:
        principal = ANONYMOUS
    else:
        token = _token(request)
        principal = auth.authenticate(token) if token else None
        if principal is None:
            raise HTTPException(
                401, "인증 필요 (Authorization: Bearer <API key>)", {"WWW-Authenticate": "Bearer"}
            )
        if not principal.has(role):
            raise HTTPException(
                403, f"'{role}' 역할이 필요합니다 (보유: {sorted(principal.roles)})"
            )
    request.state.principal = principal
    return principal


def current_principal(request: Request) -> Principal:
    return getattr(request.state, "principal", ANONYMOUS)


def actor_of(principal: Principal, claimed: str | None, default: str = "human") -> str:
    """인증 주체가 있으면 그 이름이 행위자다 — 본문의 자기 신고는 인증이 꺼졌을 때만 쓴다."""
    return principal.name if principal.authenticated else (claimed or default)


PrincipalDep = Annotated[Principal, Depends(current_principal)]


if __name__ == "__main__":  # 키 발급: python -m aiops.api.auth alice approver
    import secrets
    import sys

    name, *roles = sys.argv[1:] or ["operator-bot", "operator"]
    key = secrets.token_urlsafe(32)
    entry = ApiKey(name=name, sha256=hash_key(key), roles=[Role(r) for r in roles])
    print(f"API key (한 번만 표시, 안전하게 전달): {key}")
    print(f"AIOPS_API_KEYS 항목: {entry.model_dump_json()}")
