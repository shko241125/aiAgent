# CLAUDE.md — 최상위 규칙 (이 저장소의 모든 작업자·에이전트에 우선 적용)

이 저장소는 **칸반 보드(`tracking/`)가 단일 진실 공급원**이다. 계획·진행·인계는 모두 카드에 있고,
문서(`docs/ROADMAP.md`, `docs/BOARD.md`, `docs/plans/*` 의 AUTO 구간)는 카드와 코드에서 **생성**된다.
당신에게 이전 대화의 기억이 없어도 괜찮다 — 카드가 기억이다. (방법론: `docs/plans/0001-kanban-blackboard.md`)

## 0. 세션 시작 시
1. 세션 시작 훅이 보드 브리핑을 출력한다. 안 보이면: `python -m aiops.kanban.cli brief`
2. **진행 중(IN_PROGRESS) 카드가 있으면 그것부터** 이어받는다: `python -m aiops.kanban.cli show <ID>` 로 인계 메모·이력 확인.

## 1. 작업 규칙 (반드시)
| # | 규칙 | 명령 |
|---|---|---|
| R1 | 모든 변경은 카드에 연결된다. 해당 카드가 없으면 먼저 만든다 (상위 에픽 `R-x.y` 지정) | `add "제목" --prefix M1 --parent R-4.2 --accept ... --column ready` |
| R2 | 착수 전에 claim 한다 (담당·lease 기록) | `claim <ID>` |
| R3 | 의미 있는 진척은 note, **설계 판단은 `--decision`** 으로 남긴다 | `note <ID> "..." --decision` |
| R4 | 결과물·측정값은 outputs 에 기록한다 | `output <ID> key value` |
| R5 | DoD 항목을 실제로 충족했을 때만 체크한다 | `check <ID> 0 1` |
| R6 | 카드를 옮길 때 **handoff 메모 필수** — 기억 없는 다음 작업자가 이것만 보고 이어갈 수 있게: 현재 상태 · 한 일 · 남은 일 · 주의점 | `move <ID> done --handoff "..."` |
| R7 | 막히면 BLOCKED + 사유. 우회하거나 조용히 멈추지 않는다 | `move <ID> blocked --reason "..."` |
| R8 | 계획 변경 = 카드 수정. 생성 문서(AUTO 구간, BOARD.md)는 **직접 고치지 않는다** | `edit <ID> --desc ... --accept ...` |
| R9 | 코드 TODO 는 `TODO(x.y)` 형식, x.y 는 존재하는 에픽 ID | — |
| R10 | 커밋 메시지에 카드 ID 를 `[ID]` 로 적는다 (브리핑의 '관련 커밋'에 연결됨) | `git commit -m "[M1-03] ..."` |
| R11 | 코드 파일을 옮기거나 지우면 그 경로를 가리키는 카드 `refs` 도 고친다 (`docs check` 가 잡는다) | `edit <ID> --add-ref ...` |
| R12 | 끝내기 전 `make check` (lint + test + docs check) 통과 | `make check` |

CLI 전체: `python -m aiops.kanban.cli --help` (별칭 `kanban`, actor 기본값 `claude-code`, `$KANBAN_ACTOR` 로 변경)

## 2. 자동 연동 (사람이 잊어도 돌아가게)
| 시점 | 동작 | 위치 |
|---|---|---|
| 세션 시작 | 의존성 확인, git 훅 경로 설정, 보드 브리핑 주입 | `.claude/settings.json` → `scripts/hooks/session-start.sh` |
| 파일 편집 직후 | `docs sync` 로 생성 문서 재생성, 검증 문제는 즉시 알림 | `scripts/hooks/post-edit-sync.sh` |
| 응답 종료 시 | 검증 실패(잘못된 TODO ID·끊긴 refs)가 남아 있으면 종료를 막고 수정 요구 | `scripts/hooks/stop-check.sh` |
| git commit | 문서 재생성·스테이징, 검증 실패 시 커밋 차단 / 카드 ID 없는 메시지 경고 | `.githooks/pre-commit`, `.githooks/commit-msg` |
| CI | `docs check` — 문서가 보드와 다르면 실패 | `.github/workflows/ci.yml` |

## 3. 코드 규칙
- 외부 시스템(LLM, Vector DB, 모니터링)은 인터페이스(ABC) 뒤에 두고 테스트는 Fake/MockTransport 로.
- 상태를 바꾸는 도구는 `ToolRisk.WRITE/DESTRUCTIVE` — 승인 정책을 거친다.
- 수치 분석은 코드가, 해석은 LLM 이 (LLM 에는 fact sheet 만).
- 스타일: ruff (line 100). 테스트: `pytest -q`. 모두 `make check` 에 포함.
