"""조치 실행기 (M3-02 / 2.3).

- SimulatedExecutor  : 시뮬레이터 장애를 실제로 해소(Fault.fixed_by) → 효과 검증·E2E 평가용
- KubernetesExecutor : Kubernetes API 직접 호출 (kubectl 없이 httpx)
    restart  = Deployment pod template 에 restartedAt 주석 패치 (kubectl rollout restart 와 동일)
    scale    = /scale 서브리소스 패치 (되돌림 조치 = 이전 replica 수)
    rollback = 직전 리비전 ReplicaSet 의 pod template 으로 교체 (kubectl rollout undo 와 동일 원리)
    dry_run  = 서버측 `?dryRun=All` — API 서버가 권한·스키마·admission 까지 검증하고 저장만 안 함
"""

import copy
from abc import ABC, abstractmethod

import httpx

from aiops.domain.models import utcnow
from aiops.integrations.simulated import SimulatedOpsSource
from aiops.remediation.actions import ActionResult, ActionType, RemediationAction


class Executor(ABC):
    @abstractmethod
    async def execute(self, action: RemediationAction, dry_run: bool = False) -> ActionResult: ...

    async def current_replicas(self, action: RemediationAction) -> int | None:
        return None


class SimulatedExecutor(Executor):
    def __init__(
        self, source: SimulatedOpsSource, replicas: int = 3, recovery_min: float = 5.0
    ) -> None:
        """recovery_min: 시간 압축. 시뮬레이터 시각은 '조회 시점 기준 N분 전'의 상대 시간이라,
        조치 직후 조회해도 '조치 후 recovery_min 분이 지난' 상태로 보이게 한다
        (실 환경의 효과 검증도 몇 분 기다린 뒤 확인한다)."""
        self.source = source
        self.recovery_min = recovery_min
        self.replicas: dict[str, int] = {}
        self.default_replicas = replicas
        self.executed: list[RemediationAction] = []

    async def current_replicas(self, action: RemediationAction) -> int | None:
        return self.replicas.get(action.service, self.default_replicas)

    async def execute(self, action: RemediationAction, dry_run: bool = False) -> ActionResult:
        if action.type == ActionType.MANUAL:
            return ActionResult(
                action_id=action.id,
                ok=False,
                dry_run=dry_run,
                detail="자동화 불가 조치 — 사람이 수행해야 함",
            )
        fixes = [
            f
            for f in self.source._faults
            if f.service == action.service and action.type.value in f.fixed_by
        ]
        revert = None
        if action.type == ActionType.SCALE:
            old = await self.current_replicas(action)
            revert = action.model_copy(
                update={"params": {"replicas": old}, "reason": "scale 되돌림"}, deep=True
            )
        if dry_run:
            return ActionResult(
                action_id=action.id,
                ok=True,
                dry_run=True,
                revert=revert,
                detail=f"(dry-run) 해소 예상 장애 {len(fixes)}건",
            )
        if action.type == ActionType.SCALE:
            self.replicas[action.service] = int(action.params["replicas"])
        for f in fixes:  # 장애 해소: recovery_min 분 전에 끝난 것으로 표현 (시간 압축)
            f.duration_min = max(0.0, f.onset_min_ago - self.recovery_min)
        self.executed.append(action)
        return ActionResult(
            action_id=action.id,
            ok=True,
            dry_run=False,
            revert=revert,
            detail=f"{action.type} 실행, 해소된 장애 {len(fixes)}건",
        )


class KubernetesError(RuntimeError):
    pass


class KubernetesExecutor(Executor):
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        deployments: dict[str, str] | None = None,
        verify: bool | str = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.deployments = deployments or {}  # 서비스명 → Deployment 이름 (기본: 같은 이름)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url, headers=headers, verify=verify, timeout=15
        )

    def _path(self, a: RemediationAction, sub: str = "") -> str:
        name = self.deployments.get(a.service, a.service)
        return f"/apis/apps/v1/namespaces/{a.namespace}/deployments/{name}{sub}"

    async def _req(self, method: str, path: str, dry_run: bool = False, **kw) -> dict:
        params = {**kw.pop("params", {}), **({"dryRun": "All"} if dry_run else {})}
        resp = await self._client.request(method, path, params=params or None, **kw)
        if resp.status_code >= 400:
            raise KubernetesError(f"{method} {path} → {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def current_replicas(self, action: RemediationAction) -> int | None:
        return int((await self._req("GET", self._path(action, "/scale")))["spec"]["replicas"])

    async def execute(self, action: RemediationAction, dry_run: bool = False) -> ActionResult:
        match action.type:
            case ActionType.RESTART:
                body = {
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "kubectl.kubernetes.io/restartedAt": utcnow().isoformat()
                                }
                            }
                        }
                    }
                }
                await self._req(
                    "PATCH",
                    self._path(action),
                    dry_run,
                    json=body,
                    headers={"Content-Type": "application/strategic-merge-patch+json"},
                )
                return ActionResult(
                    action_id=action.id,
                    ok=True,
                    dry_run=dry_run,
                    detail="rollout restart 패치 적용",
                )
            case ActionType.SCALE:
                old = await self.current_replicas(action)
                target = int(action.params["replicas"])
                await self._req(
                    "PATCH",
                    self._path(action, "/scale"),
                    dry_run,
                    json={"spec": {"replicas": target}},
                    headers={"Content-Type": "application/merge-patch+json"},
                )
                revert = action.model_copy(
                    update={"params": {"replicas": old}, "reason": "scale 되돌림"}, deep=True
                )
                return ActionResult(
                    action_id=action.id,
                    ok=True,
                    dry_run=dry_run,
                    detail=f"replicas {old}→{target}",
                    revert=revert,
                )
            case ActionType.ROLLBACK:
                template, rev = await self._previous_template(action)
                patch = [{"op": "replace", "path": "/spec/template", "value": template}]
                await self._req(
                    "PATCH",
                    self._path(action),
                    dry_run,
                    json=patch,
                    headers={"Content-Type": "application/json-patch+json"},
                )
                return ActionResult(
                    action_id=action.id,
                    ok=True,
                    dry_run=dry_run,
                    detail=f"리비전 {rev} 의 pod template 으로 롤백",
                )
            case _:
                return ActionResult(
                    action_id=action.id,
                    ok=False,
                    dry_run=dry_run,
                    detail="자동화 불가 조치 — 사람이 수행해야 함",
                )

    async def _previous_template(self, action: RemediationAction) -> tuple[dict, int]:
        dep = await self._req("GET", self._path(action))
        current = int(dep["metadata"]["annotations"]["deployment.kubernetes.io/revision"])
        labels = dep["spec"]["selector"]["matchLabels"]
        selector = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        rs_list = await self._req(
            "GET",
            f"/apis/apps/v1/namespaces/{action.namespace}/replicasets",
            params={"labelSelector": selector},
        )
        owned = [
            rs
            for rs in rs_list["items"]
            if any(
                o.get("kind") == "Deployment" and o.get("name") == dep["metadata"]["name"]
                for o in rs["metadata"].get("ownerReferences", [])
            )
        ]
        want = action.params.get("to_revision")

        def rev(rs) -> int:
            return int(rs["metadata"]["annotations"]["deployment.kubernetes.io/revision"])

        candidates = [rs for rs in owned if (rev(rs) == int(want) if want else rev(rs) < current)]
        if not candidates:
            raise KubernetesError("되돌릴 이전 리비전이 없음")
        target = max(candidates, key=rev)
        template = copy.deepcopy(target["spec"]["template"])
        template["metadata"].get("labels", {}).pop("pod-template-hash", None)  # RS 전용 라벨 제거
        return template, rev(target)


class DryRunOnlyExecutor(Executor):
    """실제 변경을 절대 하지 않는 실행기 — 실 인프라 연결 전 '계획·검증만' 운영할 때."""

    async def execute(self, action: RemediationAction, dry_run: bool = False) -> ActionResult:
        return ActionResult(
            action_id=action.id,
            ok=dry_run,
            dry_run=dry_run,
            detail="(dry-run 전용 모드) 계획만 검증"
            if dry_run
            else "dry-run 전용 모드 — 실제 실행 차단 (AIOPS_REMEDIATION_EXECUTOR)",
        )


def build_executor(settings, source) -> Executor:
    import logging

    if settings.remediation_executor == "kubernetes":
        return KubernetesExecutor(
            settings.k8s_api_url, settings.k8s_token, verify=settings.k8s_ca_cert or True
        )
    if settings.remediation_executor == "simulated":
        if isinstance(source, SimulatedOpsSource):
            return SimulatedExecutor(source)
        logging.getLogger(__name__).warning("실 데이터 소스에 simulated 실행기 불가 → dry_run")
    return DryRunOnlyExecutor()
