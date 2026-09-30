# AIOps Autonomous Operations Platform

Multi-Agent AI가 운영 데이터를 실시간 분석하고, ML 기반 상황 인식(Situation Awareness)으로 이상 징후를
조기에 탐지하며, 장애 원인 분석(RCA)과 자동 대응까지 수행하는 **자율 운영(Autonomous Operations) 플랫폼**.

> **작업 전 필독: [CLAUDE.md](CLAUDE.md)** — 이 저장소의 최상위 규칙. 계획·진행은 칸반 보드(`tracking/`)가
> 단일 진실 공급원이고, [ROADMAP](docs/ROADMAP.md) · [BOARD](docs/BOARD.md) 는 보드에서 자동 생성된다.
> 기억 없는 새 작업자(사람·AI)도 `make board` 한 번으로 이어받을 수 있다.

## 빠른 시작

```bash
make install          # uv 로 .venv 생성 + 개발 의존성 설치
make test             # 단위/API 테스트
make demo             # 서버 없이 인시던트 대응 파이프라인 1회 실행
make run              # http://localhost:8000/docs (Swagger UI)
make board            # 칸반 보드 브리핑 (진행 중·막힘·다음 후보)
make check            # lint + test + 문서↔보드 동기화 검증 (커밋 전 필수)
```

실제 LLM 사용: `cp .env.example .env` 후 `AIOPS_LLM_DEFAULT_PROVIDER` 와 API 키 설정.
구축형 LLM(vLLM/Ollama의 Gemma·Qwen 등)은 `AIOPS_LLM_DEFAULT_PROVIDER=local`.

Docker 전체 스택(API + PostgreSQL + Qdrant [+ Ollama]):

```bash
docker compose up -d --build
docker compose --profile local-llm up -d   # Ollama 포함
```

## 구조 한눈에 보기

```
src/aiops/
├── core/            설정 · 로깅 · 안정성(재시도/서킷브레이커) · 의존성 조립(container)   [4.4, 4.6]
├── llm/             LLM 추상화 · OpenAI/Claude/Gemini/구축형 프로바이더 · 라우터(fallback) [4.1]
├── kanban/          칸반 공유칠판: 카드·정책·저장소·브리핑·에이전트 도구·CLI·문서 연동  [1.2, 1.6]
├── agents/
│   ├── base.py          BaseAgent / LLMAgent (도구 호출 루프)                              [1.1]
│   ├── tools/           Tool Calling Framework (@tool, 스키마 자동생성, 승인 정책)          [1.5]
│   ├── memory/          단기(대화) · 공유(Blackboard) · 장기 메모리                         [1.6]
│   ├── orchestration/   Orchestrator(Pipeline/Workflow/Supervisor), 워크플로우, LangGraph  [1.2~1.4]
│   └── specialists/     탐지 · RCA · 자동조치 · 인시던트 · 보고서 · 지식 Agent              [2.1~2.6]
├── analytics/       이상탐지 · 상황인식 · 이벤트 상관/패턴 · 장애예측 · 의사결정용 요약     [3.1~3.5]
├── rag/             청킹 · BM25 · 임베딩 · Vector DB · 하이브리드(RRF) · RAG 서비스        [4.2]
├── prompts/         프롬프트 템플릿 레지스트리 (templates/*.md)                          [4.3]
├── workflow/        DAG Workflow Engine                                                   [4.5]
├── integrations/    운영 데이터 소스 어댑터 (현재: 시뮬레이터)
├── db/              SQLAlchemy 모델 · 리포지토리                                          [4.4]
└── api/             FastAPI 라우터                                                        [4.4]
data/knowledge/      RAG 시드 지식 (runbook, 포스트모템)
tracking/            개발 칸반 보드 (카드 1장 = JSON 1개) — 계획의 원본
docs/                아키텍처 · 로드맵(생성) · 보드(생성) · 계획(plans/) · ADR
```

자세한 설계는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 주요 API

| Method | Path | 설명 |
|---|---|---|
| GET | `/health`, `/ready` | 상태 확인 |
| GET | `/api/v1/agents` | 등록된 에이전트 목록 |
| POST | `/api/v1/agents/{name}/run` | 단일 에이전트 실행 |
| POST | `/api/v1/orchestrations/incident-response` | 알람 → 탐지 → RCA → 조치 → 인시던트 → 보고서 워크플로우 |
| POST | `/api/v1/orchestrations/supervised` | Supervisor LLM 이 동적으로 에이전트 선택 |
| POST | `/api/v1/orchestrations/kanban` | Pull 방식 — 에이전트가 보드의 READY 카드를 당겨 처리 (중단 후 재개 가능) |
| GET/POST | `/api/v1/boards/{board_id}/...` | 칸반 보드 조회·카드 생성/이동/메모·브리핑·claim-next·지표 |
| POST | `/api/v1/analytics/{anomalies,situation,events,forecast}` | 이상탐지 · 상황인식 · 이벤트 분석 · 예측 |
| POST | `/api/v1/rag/{ingest,search,answer}` | 지식 인제스트 · 하이브리드 검색 · 근거 기반 답변 |
| GET/POST | `/api/v1/incidents` | 인시던트 관리 |

## 개발 규칙

[CLAUDE.md](CLAUDE.md) 참고 — 카드 claim → 작업 → handoff 와 함께 이동, 커밋 메시지에 `[카드ID]`,
문서는 보드에서 생성(`make docs`), 커밋 전 `make check`. git 훅은 `make install` 시 자동 설정된다.
