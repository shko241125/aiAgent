# 기능 로드맵 (번호 체계)

코드의 `TODO(x.y)` 주석, 커밋/PR 제목, 이슈 라벨에 아래 번호를 그대로 사용한다.

상태 표기: ✅ 최소 동작 구현 · 🧩 인터페이스/골격만 · ⬜ 미착수

---

## 1. Multi-Agent 플랫폼

| ID | 항목 | 모듈 | v0.1 | 다음 과제 | 완료 기준 |
|---|---|---|---|---|---|
| 1.1 | AI Agent 설계·개발 | `agents/base.py` | ✅ | 스트리밍 응답, 구조화 출력(JSON Schema 강제), 에이전트별 모델 지정 | 모든 특화 Agent 가 구조화 결과(`data`)를 스키마 검증 통과 |
| 1.2 | Multi-Agent 협업 구조 | `agents/orchestration/orchestrator.py`, `memory/base.py(Blackboard)` | ✅ | 에이전트 간 메시지 프로토콜(요청/응답/반박), 병렬 가설 검증(RCA 다중 에이전트 토론) | 2개 이상 Agent 가 같은 가설을 교차 검증하는 시나리오 |
| 1.3 | Agent Workflow | `agents/orchestration/workflows.py` | ✅ | 워크플로우 정의 YAML/DB 화, 장애 유형별 워크플로우 분기 | 코드 수정 없이 워크플로우 추가 |
| 1.4 | Agent Orchestration | `orchestrator.py`, `langgraph_adapter.py` | ✅ | Supervisor 라우팅 LangGraph 조건부 엣지 이식, 중단/재개(checkpoint), 실행 취소 | 실행 중 워크플로우를 승인 대기 후 재개 |
| 1.5 | Tool Calling Framework | `agents/tools/` | ✅ | MCP(Model Context Protocol) 서버/클라이언트 연동, 도구 사용량·실패율 메트릭, 도구 결과 크기 제한 | 외부 MCP 도구를 설정만으로 등록 |
| 1.6 | Memory·Context 관리 | `agents/memory/`, `agents/context.py` | ✅ | 토큰 기준 트리밍 + 요약 메모리, 장기 메모리 Vector DB 이관, 컨텍스트 압축 | 50+ step 대화에서 컨텍스트 한도 초과 없음 |

## 2. AIOps 및 운영 자동화

| ID | 항목 | 모듈 | v0.1 | 다음 과제 | 완료 기준 |
|---|---|---|---|---|---|
| 2.1 | 장애 탐지·분석 Agent | `specialists/aiops.py: DetectionAgent` | ✅ | 실제 Prometheus/Loki 어댑터, 알람 노이즈 필터(3.3 연동) | 실데이터 알람 → 장애 여부 판정 정확도 측정 |
| 2.2 | RCA Agent | `RCAAgent` | ✅ | 토폴로지 그래프 기반 원인 후보 랭킹, 변경-장애 상관 점수, 과거 유사 인시던트 검색 강화 | 과거 장애 셋에서 Top-3 원인 적중률 측정 |
| 2.3 | 운영 자동화 Agent | `RemediationAgent`, `tools/builtin/ops.py` | 🧩 | K8s/Ansible 실제 실행기, 승인 요청 채널(Slack 버튼) + 콜백, 조치 후 효과 검증 루프 | dry-run → 승인 → 실행 → 지표 회복 확인 자동화 |
| 2.4 | Incident Management Agent | `IncidentAgent`, `api/routers/incidents.py` | 🧩 | ITSM(Jira/ServiceNow) 연동, 상태 머신, 온콜 에스컬레이션, 타임라인 자동 기록 | 인시던트 생성~종료 전 과정 자동 기록 |
| 2.5 | 운영 보고서 Agent | `ReportAgent` | 🧩 | 일간/주간 리포트 스케줄러, 차트 첨부, 보고서 템플릿 다양화 | 주간 운영 리포트 자동 발송 |
| 2.6 | 운영 지식 기반 Agent | `KnowledgeAgent` | ✅ | 해결된 인시던트 → 지식 자동 축적, 답변 피드백 수집 | 운영자 Q&A 근거 인용률 > 90% |

## 3. 상황 인식 및 데이터 분석

| ID | 항목 | 모듈 | v0.1 | 다음 과제 | 완료 기준 |
|---|---|---|---|---|---|
| 3.1 | 이상 탐지 | `analytics/anomaly/` | ✅ | 스트리밍 탐지, 다변량 모델, 계절성 분해(STL), 모델 학습/버전 관리 | 라벨된 데이터셋에서 Precision/Recall 리포트 |
| 3.2 | ML 기반 Situation Awareness | `analytics/situation.py` | ✅(규칙) | 가중치 학습(과거 인시던트), 토폴로지 영향 전파, 서비스 헬스 스코어 대시보드 | 규칙 대비 ML 버전 오탐률 감소 |
| 3.3 | 이벤트 분석·패턴 탐지 | `analytics/events.py` | ✅ | 로그 템플릿 추출(Drain3), 순차 패턴 마이닝, 알람 폭주 억제 | 알람 압축률(원본 대비 클러스터 수) 측정 |
| 3.4 | 장애 예측·위험도 분석 | `analytics/prediction.py` | ✅(선형) | 장애 확률 분류 모델, 용량 예측, 예측 기반 선제 알람 | N분 전 예측 적중률 측정 |
| 3.5 | Agent 의사결정용 데이터 분석 | `analytics/insights.py` | ✅ | 메트릭 간 상관 분석, 변화점 탐지(change point), fact sheet 표준 스키마 | fact sheet 로 LLM 입력 토큰 X% 절감 |

## 4. AI 서비스 Backend

| ID | 항목 | 모듈 | v0.1 | 다음 과제 | 완료 기준 |
|---|---|---|---|---|---|
| 4.1 | LLM 기반 AI 서비스 | `llm/` | ✅ | Gemini tool calling, 스트리밍, 토큰·비용 집계, 구축형 모델(Gemma/Qwen) 벤치마크 | 동일 시나리오 모델별 품질/지연/비용 비교표 |
| 4.2 | RAG 시스템 | `rag/` | ✅ | 실 임베딩(bge-m3 등) 적용, Reranker, 한국어 형태소 분석기 비교, 검색 평가셋(Recall@k, MRR) | 평가셋 기준 하이브리드 > 단일 검색 입증 |
| 4.3 | Prompt Engineering / KB | `prompts/`, `data/knowledge/` | ✅ | 프롬프트 버전별 eval 자동화, few-shot 예시 관리, 지식 수집 파이프라인 | 프롬프트 변경 시 회귀 eval CI |
| 4.4 | FastAPI Backend | `api/`, `db/` | ✅ | 인증/인가(API Key·OIDC), Alembic 마이그레이션, 비동기 작업 큐(장시간 워크플로우) | 운영 배포 가능한 API |
| 4.5 | Workflow Engine | `workflow/engine.py` | ✅ | 상태 영속화·재개, 승인 대기 단계, 스케줄 트리거, 타임아웃 정책 | 프로세스 재시작 후 워크플로우 재개 |
| 4.6 | 성능 최적화·안정성 | `core/resilience.py`, `api/middleware.py` | 🧩 | /metrics(Prometheus), OpenTelemetry 트레이싱, LLM 응답 캐시, 부하 테스트(Locust) | p95 지연·에러율 SLO 정의 및 대시보드 |

---

## 마일스톤 (권장 순서)

| 단계 | 목표 | 포함 ID |
|---|---|---|
| **M0** (완료) | 골격 — 전 영역 인터페이스 + Fake LLM 으로 E2E 동작 | 전체 |
| **M1** | 실 LLM + 실 지식으로 RCA 품질 확보 | 4.1, 4.2, 4.3, 2.2, 2.6 |
| **M2** | 실데이터 연동과 상황 인식 고도화 | 2.1, 3.1, 3.2, 3.3, 3.5 |
| **M3** | 안전한 자동 조치 (HITL) | 2.3, 2.4, 1.4, 4.5 |
| **M4** | 예측·보고·운영 안정화 | 3.4, 2.5, 4.4, 4.6, 1.6 |
