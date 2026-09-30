# PLAN-0001: 칸반 공유칠판 (Kanban Blackboard) — 메모리 없는 에이전트도 이어받는 작업 추적

- 상태·진행: 아래 표는 보드에서 자동 생성된다 (전체 보드: [BOARD.md](../BOARD.md))
- 관련 로드맵: 1.2 (협업 구조), 1.4 (오케스트레이션), 1.6 (Memory·Context)

## 1. 문제

v0.1 의 Blackboard 는 `'<agent>.<field>'` 키-값 저장소다. 한 번의 실행 안에서는 충분하지만,

1. **작업 단위가 없다** — 무엇이 끝났고, 무엇이 남았고, 누가 하는 중인지 알 수 없다.
2. **실행이 끊기면 복구할 수 없다** — 에이전트가 죽거나 새 세션이 시작되면 그동안의 맥락이 사라진다.
3. **인계(handoff)가 암묵적이다** — 다음 에이전트는 이전 결과를 "알아서" 찾아 읽어야 한다.

## 2. 핵심 아이디어: "카드가 곧 기억이다 (Card as Memory)"

에이전트는 **기억을 갖지 않는다(stateless)**. 대신 모든 맥락을 **카드**에 외부화(externalize)한다.
어떤 에이전트든 카드 하나만 읽으면 작업을 이어받을 수 있어야 한다 — 이것이 설계의 합격 기준이다.

> 비유: 병원 교대 근무. 간호사는 전 근무자의 기억에 기대지 않고 **환자 차트**를 보고 이어서 돌본다.
> 차트에는 현재 상태·지금까지 한 처치·다음에 할 일·주의사항이 있다. 카드가 차트다.

## 3. 칸반 방법론 적용

| 칸반 원칙 | 에이전트 협업에서의 의미 | 구현 |
|---|---|---|
| 작업 시각화 | 모든 작업 단위는 카드, 상태는 컬럼 | `Card.column` ∈ BACKLOG · READY · IN_PROGRESS · REVIEW · BLOCKED · DONE |
| WIP 제한 | 에이전트/컬럼별 동시 작업 수 제한 → 과부하·경합 방지 | `BoardPolicy.wip_limits`, `per_assignee_wip` |
| 당김(Pull) 시스템 | 오케스트레이터가 밀어넣지 않고, 에이전트가 능력에 맞는 카드를 **당겨** 간다 | `claim_next(actor, capabilities)` |
| 명시적 정책 | 컬럼 이동 규칙을 코드로 강제 | 전이 표, handoff 필수, DoD 체크, 선행 카드 완료 |
| 흐름 관리 | 막힌 작업(BLOCKED)과 사유를 드러냄, 리드/사이클 타임 측정 | `blocked_reason`, `BoardMetrics` |
| 피드백 루프 | 모든 행동이 카드 로그에 남음 → 회고·평가 | append-only `Card.log` |

### 3.1 카드 구조 (차트 항목)

| 필드 | 역할 |
|---|---|
| `title`, `description`, `type`(epic/task/bug/investigation), `priority`(p0~p3) | 무엇을 왜 |
| `acceptance[]` | 완료 조건(DoD) 체크리스트 — 모두 체크돼야 DONE |
| `capabilities[]` | 이 카드를 당겨갈 수 있는 에이전트 능력 (예: `rca`) |
| `parent_id`, `depends_on[]` | 에픽-하위 작업, 선행 관계 |
| `assignee`, `lease_expires_at` | 현재 담당자와 **임대(lease)** 만료 시각 |
| `handoff` | **다음 담당자에게 남기는 인계 메모** (가장 중요) |
| `outputs{}` | **카드 단위 공유칠판** — 결과물 키-값 (기존 Blackboard 를 작업 단위로 분할) |
| `log[]` | 행동 이력 (claim/move/note/decision/output/block/lease_expired) |
| `refs[]` | 관련 로드맵 ID·파일 경로 (문서 자동 연동에 사용) |
| `version` | 낙관적 동시성 제어 (동시에 두 에이전트가 같은 카드를 잡는 것 방지) |

### 3.2 컬럼 전이 규칙

```
BACKLOG ──▶ READY ──claim──▶ IN_PROGRESS ──▶ REVIEW ──▶ DONE
   ▲          ▲  ◀─release/lease 만료─┘  │  ▲      │        │
   └──────────┴────────────────────────  ▼  │      ▼        │ reopen
                                      BLOCKED ◀──────────────┘
```

- `IN_PROGRESS` 진입은 claim 으로만 (담당자·lease 설정), 담당자별 WIP 제한 검사.
- `IN_PROGRESS` 에서 나갈 때(REVIEW/BLOCKED/DONE/READY) **handoff 메모 필수**.
- `DONE` 진입: DoD 전부 체크 + (에픽이면) 하위 카드 전부 DONE.
- `BLOCKED` 진입: `blocked_reason` 필수 (예: 사람 승인 대기, API 키 없음).
- lease 만료 카드는 `reap_expired()` 가 READY 로 되돌린다 — 로그·handoff·outputs 는 그대로 보존.

### 3.3 브리핑 (메모리 없는 에이전트의 진입점)

`board.briefing(card_id)` 는 카드 하나로 작업을 재개하는 데 필요한 모든 것을 한 문서로 만든다:

1. 카드 요약(상태·담당·우선순위) 2. 목표·DoD 체크리스트 3. 상위 에픽 요약
4. **선행 카드들의 handoff 와 outputs** 5. **이전 담당자의 인계 메모** 6. 최근 작업 이력
7. 카드 공유칠판(outputs) 8. **지금 해야 할 일** (컬럼별 안내)

에이전트 실행 시 이 브리핑이 프롬프트에 자동으로 주입된다.

## 4. 협업 패턴 추가: Kanban (Pull)

v0.1 의 Pipeline / Workflow(DAG) / Supervisor 에 네 번째 패턴을 추가한다.

```
Planner(LLM 또는 코드) ─ 목표를 카드로 분해 ─▶ [READY 카드들]
                                              │ claim_next(capabilities)
       detection ◀──┬── rca ◀──┬── remediation ◀┘   (각자 능력에 맞는 카드를 당겨감)
                    └── 결과는 card.outputs, 인계는 card.handoff
```

- 인시던트 워크플로우는 인시던트마다 보드를 만들고 **에픽 1 + 단계별 카드**를 생성한다.
  각 단계는 카드를 claim → 실행 → handoff 와 함께 DONE/BLOCKED 로 이동한다.
- 승인이 필요한 도구 호출(PENDING_APPROVAL)은 카드를 **BLOCKED** 로 옮기고 사유를 남긴다 →
  사람이 승인 후 READY 로 돌리면 새 에이전트가 브리핑을 읽고 이어서 수행한다.

## 5. 저장소 계층

| 구현 | 용도 |
|---|---|
| `InMemoryBoardStore` | 테스트 |
| `SqlBoardStore` | 플랫폼 런타임 (인시던트 보드). 프로세스 재시작 후에도 유지 |
| `FileBoardStore` | **저장소 자체의 개발 이슈 추적** (`tracking/cards/*.json`, git 으로 버전 관리) |

세 구현 모두 같은 `KanbanBoard` 서비스·정책을 공유한다. → 런타임 에이전트와 개발 에이전트(Claude Code 세션)가
**같은 방법론**으로 일한다 (dogfooding).

## 6. 문서 자동 연동 (최상위 규칙)

단일 진실 공급원(Single Source of Truth) = `tracking/` 보드. 문서는 **생성물**이다.

```
tracking/cards/*.json ─┐
src/**  TODO(x.y)     ─┼─▶ python -m aiops.kanban.cli docs sync ─▶ docs/ROADMAP.md (AUTO 구간)
card.refs (파일 경로)  ─┘                                         └▶ docs/BOARD.md
```

- 에픽 카드 `R-x.y` = 로드맵 항목. 하위 카드 진행률·열린 `TODO(x.y)` 수가 ROADMAP 에 자동 반영된다.
- `docs check` 는 다음을 검증하고 실패 시 CI 를 막는다:
  생성 문서가 최신인가 / `TODO(x.y)` 가 존재하는 에픽을 가리키는가 / 카드 `refs` 경로가 실제로 존재하는가
  (→ 코드 이동·삭제 시 계획 문서가 반드시 함께 갱신됨).
- 자동 실행 지점: Claude Code 훅(편집 후 sync, 세션 시작 시 보드 브리핑), git pre-commit, CI.
- 규칙 원문: 저장소 루트 [`CLAUDE.md`](../../CLAUDE.md).

## 7. 구현 단계 (카드 — 자동 연동)

<!-- AUTO:CARDS label=plan:0001 -->
| 카드 | 제목 | 상태 | 담당 | 인계 메모 / 막힌 사유 |
|---|---|---|---|---|
| KB-01 | 칸반 도메인 모델·정책 (컬럼·전이·WIP·lease·DoD) | ✅ done | claude-code | models.py/policy.py 완료. 전이표 ALLOWED_TRANSITIONS, WIP(컬럼/담당자), lease, DoD. 테스트: tests/unit/kanban/test_board.py |
| KB-02 | 칸반 저장소 3종 (memory/file/SQL) + 낙관적 동시성 | ✅ done | claude-code | stores.py: InMemory/File/Sql. 개발 보드는 FileBoardStore(tracking/cards). 파일 저장소는 단일 프로세스 가정 — 동시 다중 쓰기는 git merge 로 해소 |
| KB-03 | 브리핑 · 에이전트용 칸반 도구 · ToolRuntime 주입 | ✅ done | claude-code | briefing.py(렌더), tools.py(board_* 7종), ToolRuntime 은 agents/tools/base.py. LLMAgent 가 task.card_id 있으면 브리핑 주입 + 보드 도구 개방 |
| KB-04 | 오케스트레이터 통합 (카드 수명주기·Pull 루프·인시던트 보드) + API | ✅ done | claude-code | Orchestrator.run_agent 가 claim→실행→outputs→DONE/REVIEW/BLOCKED. run_kanban(Pull)+plan_cards(Planner). build_incident_res… |
| KB-05 | 개발 보드 이관 · 문서 자동 연동 · CLAUDE.md · 훅 · CI | ✅ done | claude-code | CLAUDE.md(최상위 규칙 R1~R12), .claude/settings.json(SessionStart/PostToolUse/Stop), .githooks(pre-commit 문서 재생성·스테이징, commi… |

진행: 5/5
<!-- /AUTO -->

## 8. 검증 기준

- 에이전트 A 가 카드를 잡고 로그·handoff 를 남긴 뒤 사라짐(lease 만료) → **새로 만든** 에이전트 B 가
  `claim_next` 로 같은 카드를 받아 브리핑만으로 A 의 진행 내용을 확인한다 (테스트로 고정).
- 인시던트 대응 실행 후 보드에 에픽+단계 카드가 남고, 승인 대기 시 BLOCKED 로 표시된다.
- 카드 상태를 바꾸면 `docs sync` 로 ROADMAP/BOARD 가 갱신되고, 갱신하지 않으면 `docs check` 가 실패한다.
