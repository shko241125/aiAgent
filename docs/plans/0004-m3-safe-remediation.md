# PLAN-0004: M3 — 안전한 자동 조치 (Human-in-the-loop)

- 범위: 로드맵 2.3 · 2.4 · 1.4 · 4.5
- 진행: 아래 카드 표는 보드에서 자동 생성된다 (전체 보드: [BOARD.md](../BOARD.md))

## 1. 목표

M1·M2 는 "무엇이 왜 고장 났는가"를 답하게 했다. M3 은 **고치는 것** — 단, 사람이 통제권을 잃지 않는 방식으로.

```
탐지 → RCA → 조치 계획 ─▶ [사람 승인] ─▶ 실행(가드레일) ─▶ 효과 검증 ─┬▶ 복구 → 해결
                         (대기 중 프로세스가 죽어도 재개)              └▶ 미복구 → 되돌림 + 에스컬레이션
```

## 2. 설계 판단

| 판단 | 이유 |
|---|---|
| **워크플로우를 DB 에 체크포인트, 승인 단계에서 일시정지** | 승인은 몇 분~몇 시간 걸린다. 그동안 프로세스를 붙잡아 둘 수 없고 재시작될 수도 있다. 상태를 저장하고 승인 이벤트가 오면 **재개**한다 (durable execution). |
| **재개 시 공유 상태는 단계 결과에서 재구성** | 메모리(Blackboard)는 재시작으로 사라진다. 이미 끝난 단계의 결과가 저장돼 있으므로 그것으로 다시 채운다 — PLAN-0001 "기록이 곧 기억" 원칙의 연장. |
| **결정적 플레이북 + LLM 계획의 이중 구조** | 조치 후보는 RCA 후보 종류(변경·시그니처·자원)에 매핑된 플레이북이 먼저 낸다. LLM 은 설명·보완만 한다. 실행 가능한 조치 종류는 코드가 정한 화이트리스트뿐이다. |
| **가드레일은 실행기 앞에서 강제** | 허용 네임스페이스·금지 서비스(DB 등)·시간당 조치 수·스케일 배율·쿨다운. LLM·사람 실수 모두 같은 관문을 지난다. |
| **Kubernetes 서버측 dry-run 활용** | `?dryRun=All` 은 API 서버가 검증(권한·스키마·admission)까지 하고 저장만 안 한다 → 승인 화면에 "실제로 적용 가능한 계획"을 보여준다. |
| **조치 후 효과 검증은 '진행 중 이상'의 소멸로 판단** | M1-08 의 ongoing 판정을 재사용. 복구되지 않으면 되돌릴 수 있는 조치는 되돌리고 사람에게 넘긴다. |
| **승인 채널은 인터페이스 뒤에** (Slack·웹훅·로그) | Slack 상호작용 콜백은 서명(HMAC)·재생 공격(타임스탬프)을 검증한다. 미응답 승인은 에스컬레이션 후 만료되면 자동 거부(안전한 기본값). |
| **실 클러스터 검증은 별도 카드** | 이 환경엔 Kubernetes 가 없다. 계약 테스트로 경로를 고정하고 실측은 인프라가 주어지면 수행한다. |

## 3. 카드

<!-- AUTO:CARDS label=plan:0004 -->
| 카드 | 제목 | 상태 | 담당 | 인계 메모 / 막힌 사유 |
|---|---|---|---|---|
| M3-01 | 워크플로우 상태 영속화·재개 + 승인 대기(HITL) 단계 | ✅ done | claude-code | workflow/engine.py: StepStatus/RunStatus.WAITING, Step(kind=approval, describe), RunStore(InMemory)·ApprovalGate(InMemo… |
| M3-02 | 조치 실행기(시뮬레이터·Kubernetes) + 가드레일 | ✅ done | claude-code | remediation/actions.py(ActionType restart·scale·rollback·manual, risk), guardrails.py(GuardrailPolicy·Guardrails, glob … |
| M3-03 | 조치 플레이북 + 효과 검증 + 되돌림·에스컬레이션 | ✅ done | claude-code | remediation/playbook.py(change→rollback, 시그니처별 매핑, cpu→scale×2, 위험·불가 원인→manual), verify.py(verify_recovery: ongoing 이상… |
| M3-04 | 인시던트 상태 머신 + 타임라인 | ✅ done | claude-code | services/incidents.py(TRANSITIONS, IncidentService.transition/record/timeline/mttr_minutes, TimelineEvent), db Incident… |
| M3-05 | 승인 요청 저장소·API + Slack 승인 채널 + 만료·에스컬레이션 | ✅ done | claude-code | db ApprovalRow(결정적 id=apr-<run>-<step> 로 멱등), services/approvals.py(ApprovalService=엔진 ApprovalGate+decide/find/sweep/a… |
| M3-06 | 인시던트 대응 워크플로우 v2 (계획→승인→실행→검증) E2E + 칸반 연동 | ✅ done | claude-code | services/incident_response.py v2(WORKFLOW=incident_response_v2: detect→rca→plan→approve→execute(+verify)→incident(after… |
| M3-07 | 실 Kubernetes 클러스터 조치 검증 | ⛔ blocked | claude-code | 실 Kubernetes 클러스터(또는 kind/minikube + Docker) 필요 |

진행: 6/7
<!-- /AUTO -->

## 4. 완료 판정

- 모든 카드 DoD 체크 + `make check` 통과, 측정값은 각 카드 outputs 에 기록.
- E2E: 시뮬레이터 장애 → 승인 대기 중 '재시작' → 승인 → 실행 → 지표 회복 확인 → 인시던트 해결까지 테스트로 고정.

## 5. 결과 요약 (시뮬레이터 기준, 2026-10-02)

| 카드 | 결과 | 해석 |
|---|---|---|
| M3-01 영속화·재개 | 승인 대기 일시정지·재시작 후 재개·실행 중 사망 단계 재실행(at-least-once) 테스트 | 단계는 멱등이어야 한다. 재개 동시성은 프로세스 내 lock — 다중 인스턴스는 R-4.5 다음 과제 |
| M3-02 실행기·가드레일 | Kubernetes restart/scale/rollback·서버측 dry-run 계약 테스트, 가드레일 6종 | **실 클러스터 미검증(M3-07 ⛔)**. 가드레일 이력은 메모리 — 재시작 시 초기화 |
| M3-03 플레이북·검증 | 시나리오 8 × 시드 10: 1차 조치 정확도 **1.0**, 복구율 **1.0**, 자동화 금지 안전 위임 **1.0** | 시나리오·플레이북 동시 작성 → 회귀 기준선. 오판 위험은 승인·검증·되돌림으로 방어 |
| M3-04 상태 머신·타임라인 | 알람~해결 전 과정 자동 기록, MTTR | CLOSED 는 종결(재발은 새 인시던트) |
| M3-05 승인 | 웹·Slack(서명 검증) 승인, 15분 에스컬레이션·60분 만료 자동 거부 | **승인 API 인증 없음 — 운영 전 필수(TODO(4.4))** |
| M3-06 v2 E2E | 승인 복구 / 재시작 후 재개 / 거부 / 오진 미복구·에스컬레이션 4종 E2E | 재개 후 에이전트가 복원된 기억(RCA·조치 결과)을 받는 것까지 검증 |

M3 종료 조건: M3-07 해제(스테이징 클러스터에서 승인→조치→복구 1회) + 승인 API 인증(R-4.4).
