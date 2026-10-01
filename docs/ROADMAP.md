# 기능 로드맵 (번호 체계)

코드의 `TODO(x.y)` 주석, 커밋 메시지의 `[카드ID]`, 카드의 상위 에픽(`R-x.y`)에 아래 번호를 그대로 사용한다.

> 이 문서의 표는 **자동 생성**된다. 원본은 `tracking/cards/R-*.json`(에픽)과 하위 카드이며,
> 상태·하위 카드 진행률·열린 TODO 수는 보드와 코드에서 계산된다. 표를 직접 고치지 말고 카드를 고칠 것
> (`python -m aiops.kanban.cli edit R-2.2 --desc "..."`). 규칙: [CLAUDE.md](../CLAUDE.md), 방법론: [PLAN-0001](plans/0001-kanban-blackboard.md)

상태 표기: ✅ 완료 · 🔄 진행중(하위 카드 착수) · ⛔ 막힘 · 🟢 최소 동작 구현 · 🧩 인터페이스/골격만 · ⬜ 미착수

## 기능 영역

<!-- AUTO:ROADMAP -->
### 1. Multi-Agent 플랫폼

| ID | 항목 | 모듈 | 상태 | 하위 카드 | 열린 TODO | 다음 과제 | 완료 기준 |
|---|---|---|---|---|---|---|---|
| 1.1 | AI Agent 설계·개발 | `src/aiops/agents/base.py` | 🟢 최소구현 | - | 0 | 스트리밍 응답, 나머지 에이전트(incident/report/knowledge) 출력 스키마 | 모든 특화 Agent 가 구조화 결과(data)를 스키마 검증 통과 |
| 1.2 | Multi-Agent 협업 구조 | `src/aiops/agents/orchestration/orchestrator.py`, `src/aiops/kanban/` | 🟢 최소구현 (+3 완료) | 3/3 | 0 | 에이전트 간 메시지 프로토콜(요청/응답/반박), 병렬 가설 검증(RCA 다중 에이전트 토론) | 2개 이상 Agent 가 같은 가설을 교차 검증하는 시나리오 |
| 1.3 | Agent Workflow | `src/aiops/agents/orchestration/workflows.py` | 🟢 최소구현 | - | 0 | 워크플로우 정의 YAML/DB 화, 장애 유형별 워크플로우 분기 | 코드 수정 없이 워크플로우 추가 |
| 1.4 | Agent Orchestration | `src/aiops/agents/orchestration/orchestrator.py`, `src/aiops/agents/orchestration/langgraph_adapter.py` | 🟢 최소구현 (+1 완료) | 1/1 | 1 | Supervisor 라우팅 LangGraph 조건부 엣지 이식, 중단/재개(checkpoint), 실행 취소 | 실행 중 워크플로우를 승인 대기 후 재개 |
| 1.5 | Tool Calling Framework | `src/aiops/agents/tools/` | 🟢 최소구현 | - | 0 | MCP 서버/클라이언트 연동, 도구 사용량·실패율 메트릭, 도구 결과 크기 제한 | 외부 MCP 도구를 설정만으로 등록 |
| 1.6 | Memory·Context 관리 | `src/aiops/agents/memory/`, `src/aiops/agents/context.py`, `src/aiops/kanban/briefing.py` | 🟢 최소구현 (+1 완료) | 1/1 | 2 | 토큰 기준 트리밍 + 요약 메모리, 장기 메모리 Vector DB 이관, 컨텍스트 압축 | 50+ step 대화에서 컨텍스트 한도 초과 없음 |

### 2. AIOps 및 운영 자동화

| ID | 항목 | 모듈 | 상태 | 하위 카드 | 열린 TODO | 다음 과제 | 완료 기준 |
|---|---|---|---|---|---|---|---|
| 2.1 | 장애 탐지·분석 Agent | `src/aiops/agents/specialists/aiops.py` | 🟢 최소구현 | 0/4 | 0 | 실제 Prometheus/Loki 어댑터, 알람 노이즈 필터(3.3 연동) | 실데이터 알람 → 장애 여부 판정 정확도 측정 |
| 2.2 | RCA Agent | `src/aiops/agents/specialists/aiops.py` | 🟢 최소구현 (+1 완료) | 1/1 | 0 | 실 장애 이력으로 랭킹 재검증(합성 시나리오 과적합 해소), 다중 에이전트 교차 검증(1.2), 트레이스 기반 신호 추가 | 과거 장애 셋에서 Top-3 원인 적중률 측정 |
| 2.3 | 운영 자동화 Agent | `src/aiops/agents/tools/builtin/ops.py` | 🧩 골격 | - | 3 | K8s/Ansible 실제 실행기, 승인 요청 채널(Slack) + 콜백, 조치 후 효과 검증 루프 | dry-run → 승인 → 실행 → 지표 회복 확인 자동화 |
| 2.4 | Incident Management Agent | `src/aiops/api/routers/incidents.py` | 🧩 골격 | - | 1 | ITSM(Jira/ServiceNow) 연동, 상태 머신, 온콜 에스컬레이션, 타임라인 자동 기록 | 인시던트 생성~종료 전 과정 자동 기록 |
| 2.5 | 운영 보고서 Agent | `src/aiops/prompts/templates/report.md` | 🧩 골격 | - | 0 | 일간/주간 리포트 스케줄러, 차트 첨부, 보고서 템플릿 다양화 | 주간 운영 리포트 자동 발송 |
| 2.6 | 운영 지식 기반 Agent | `src/aiops/prompts/templates/knowledge.md` | 🟢 최소구현 (+1 완료) | 1/1 | 0 | 인용률 실측(M1-10), 답변 피드백 수집, 축적 지식 품질 관리(중복·노후 문서) | 운영자 Q&A 근거 인용률 > 90% |

### 3. 상황 인식 및 데이터 분석

| ID | 항목 | 모듈 | 상태 | 하위 카드 | 열린 TODO | 다음 과제 | 완료 기준 |
|---|---|---|---|---|---|---|---|
| 3.1 | 이상 탐지 | `src/aiops/analytics/anomaly/` | 🟢 최소구현 | 0/1 | 2 | 스트리밍 탐지, 다변량 모델, 계절성 분해(STL), 모델 학습/버전 관리 | 라벨된 데이터셋에서 Precision/Recall 리포트 |
| 3.2 | ML 기반 Situation Awareness | `src/aiops/analytics/situation.py` | 🟢 최소구현 | 0/1 | 1 | 가중치 학습(과거 인시던트), 토폴로지 영향 전파, 서비스 헬스 스코어 | 규칙 대비 ML 버전 오탐률 감소 |
| 3.3 | 이벤트 분석·패턴 탐지 | `src/aiops/analytics/events.py` | 🟢 최소구현 | 0/1 | 1 | 로그 템플릿 추출(Drain3), 순차 패턴 마이닝, 알람 폭주 억제 | 알람 압축률(원본 대비 클러스터 수) 측정 |
| 3.4 | 장애 예측·위험도 분석 | `src/aiops/analytics/prediction.py` | 🟢 최소구현 | - | 1 | 장애 확률 분류 모델, 용량 예측, 예측 기반 선제 알람 | N분 전 예측 적중률 측정 |
| 3.5 | Agent 의사결정용 데이터 분석 | `src/aiops/analytics/insights.py` | 🟢 최소구현 | 0/1 | 0 | 메트릭 간 상관 분석, 변화점 탐지, fact sheet 표준 스키마 | fact sheet 로 LLM 입력 토큰 절감률 측정 |

### 4. AI 서비스 Backend

| ID | 항목 | 모듈 | 상태 | 하위 카드 | 열린 TODO | 다음 과제 | 완료 기준 |
|---|---|---|---|---|---|---|---|
| 4.1 | LLM 기반 AI 서비스 | `src/aiops/llm/` | ⛔ 막힘 | 3/4 | 1 | 구축형 모델(Gemma/Qwen)·상용 모델 품질/지연/비용 벤치마크(M1-10), 스트리밍, 프롬프트 캐싱 | 동일 시나리오 모델별 품질/지연/비용 비교표 |
| 4.2 | RAG 시스템 | `src/aiops/rag/` | 🟢 최소구현 (+3 완료) | 3/3 | 5 | 실 임베딩(bge-m3)으로 재측정해 의역형 Recall 개선 확인, 한국어 형태소 분석기(kiwi) 비교, Reranker 효과 측정 | ☑ 평가셋 기준 하이브리드 > 단일 검색 입증 |
| 4.3 | Prompt Engineering / KB | `src/aiops/prompts/`, `data/knowledge/` | 🟢 최소구현 (+1 완료) | 1/1 | 1 | few-shot 예시 관리 체계, 실모델 eval 결과의 프롬프트 버전별 추적, 지식 수집 파이프라인 | ☑ 프롬프트 변경 시 회귀 eval CI |
| 4.4 | FastAPI Backend | `src/aiops/api/`, `src/aiops/db/` | 🟢 최소구현 | - | 1 | 인증/인가(API Key·OIDC), Alembic 마이그레이션, 비동기 작업 큐 | 운영 배포 가능한 API |
| 4.5 | Workflow Engine | `src/aiops/workflow/engine.py` | 🟢 최소구현 | - | 1 | 상태 영속화·재개, 승인 대기 단계, 스케줄 트리거, 타임아웃 정책 | 프로세스 재시작 후 워크플로우 재개 |
| 4.6 | 성능 최적화·안정성 | `src/aiops/core/resilience.py`, `src/aiops/api/middleware.py` | 🧩 골격 | - | 3 | /metrics(Prometheus), OpenTelemetry 트레이싱, LLM 응답 캐시, 부하 테스트 | p95 지연·에러율 SLO 정의 및 대시보드 |
<!-- /AUTO -->

## 마일스톤

<!-- AUTO:MILESTONES -->
| 단계 | 목표 | 포함 ID | 카드 진행 | 상태 |
|---|---|---|---|---|
| **M0** | 골격 — 전 영역 인터페이스 + Fake LLM 으로 E2E 동작 | 전체 | - | ✅ 완료 |
| **M1** | 실 LLM + 실 지식으로 RCA 품질 확보 | 4.1, 4.2, 4.3, 2.2, 2.6 | 9/10 | 🔄 진행중 |
| **M2** | 실데이터 연동과 상황 인식 고도화 | 2.1, 3.1, 3.2, 3.3, 3.5 | 0/8 | ⬜ 대기 |
| **M3** | 안전한 자동 조치 (HITL) | 2.3, 2.4, 1.4, 4.5 | - | ⬜ 대기 |
| **M4** | 예측·보고·운영 안정화 | 3.4, 2.5, 4.4, 4.6, 1.6 | - | ⬜ 대기 |
<!-- /AUTO -->

마일스톤별 상세 계획: [PLAN-0002 (M1)](plans/0002-m1-rca-quality.md) · [PLAN-0003 (M2)](plans/0003-m2-situation-awareness.md)
