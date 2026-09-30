# AIOps Autonomous Operations Platform

Multi-Agent AI가 운영 데이터를 실시간 분석하고, ML 기반 상황 인식(Situation Awareness)으로 이상 징후를
조기에 탐지하며, 장애 원인 분석(RCA)과 자동 대응까지 수행하는 **자율 운영(Autonomous Operations) 플랫폼**.

> 현재 단계: **v0.1 — 골격(scaffold)**. 모든 기능 영역의 인터페이스와 최소 동작 구현이 들어가 있고,
> API 키 없이(Fake LLM) 전체 흐름이 끝까지 돈다. 기능별 진행 현황은 [docs/ROADMAP.md](docs/ROADMAP.md).

## 빠른 시작

```bash
make install          # uv 로 .venv 생성 + 개발 의존성 설치
make test             # 단위/API 테스트
make demo             # 서버 없이 인시던트 대응 파이프라인 1회 실행
make run              # http://localhost:8000/docs (Swagger UI)
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
docs/                아키텍처 · 로드맵 · ADR
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
| POST | `/api/v1/analytics/{anomalies,situation,events,forecast}` | 이상탐지 · 상황인식 · 이벤트 분석 · 예측 |
| POST | `/api/v1/rag/{ingest,search,answer}` | 지식 인제스트 · 하이브리드 검색 · 근거 기반 답변 |
| GET/POST | `/api/v1/incidents` | 인시던트 관리 |

## 개발 규칙

- 브랜치 → PR → CI(ruff + pytest) 통과 후 머지.
- 새 기능은 ROADMAP 의 번호(예: `2.3`)를 커밋/PR 제목과 코드 `TODO(2.3)` 주석에 표기해 추적한다.
- 외부 시스템(LLM, Vector DB, 모니터링)은 반드시 인터페이스(ABC) 뒤에 두고, 테스트는 Fake 구현으로 작성한다.
- 상태를 바꾸는 도구는 `ToolRisk.WRITE/DESTRUCTIVE` 로 선언해 승인 정책을 거치게 한다.
