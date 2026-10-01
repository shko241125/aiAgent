# PLAN-0003: M2 — 실데이터 연동과 상황 인식 고도화

- 범위: 로드맵 2.1 · 3.1 · 3.2 · 3.3 · 3.5
- 진행: 아래 카드 표는 보드에서 자동 생성된다 (전체 보드: [BOARD.md](../BOARD.md))

## 1. 목표

M1 이 "LLM 이 맞는 답을 내는가"를 재는 도구를 만들었다면, M2 는 **LLM 앞단의 수치 분석을 실데이터에 붙이고,
그 정확도를 숫자로 재는 것**이다. 원칙 그대로 — 수치는 코드가, 해석은 LLM 이.

## 2. 설계 판단

| 판단 | 이유 |
|---|---|
| **표준 HTTP API 로 직접 연동** (Prometheus `query_range`, Loki `query_range`) | 두 API 는 사실상 업계 표준(VictoriaMetrics·Thanos·Mimir 호환). SDK 없이 httpx 로 붙이고 MockTransport 계약 테스트로 고정한다. |
| **소스 조합(Composite) + 설정 선택** | 메트릭은 Prometheus, 로그는 Loki, 변경 이력은 웹훅으로 수집한 DB, 토폴로지는 파일 — 출처가 제각각이다. 에이전트는 하나의 인터페이스만 본다. |
| **이벤트는 밀어넣기(push) 수집** | Alertmanager·CI/CD 는 웹훅을 보낸다. 폴링 대신 수신 엔드포인트 + DB 저장 → RCA 의 change 신호 원천. |
| **라벨 데이터로 탐지기를 평가한 뒤 개선** (3.1, 3.2) | 개선 전에 Precision/Recall 기준선을 먼저 잰다. 실 라벨이 없으므로 패턴별(스파이크·레벨시프트·드리프트·계절성) 합성 라벨셋을 쓰고, 한계를 명시한다. |
| **로그 템플릿은 Drain 직접 구현** (3.3) | 의존성 없이 수십 줄로 구현 가능한 고정 깊이 파스 트리 알고리즘. 로그 폭주를 '템플릿 단위 이벤트'로 압축해 RCA 시그니처 매칭의 입력이 된다. |
| **상황 인식 가중치는 로지스틱 회귀(numpy)** (3.2) | 해석 가능한 계수 → 운영자가 "왜 CRITICAL 인가"를 설명받을 수 있다. 규칙 기반과 같은 데이터로 오탐률을 비교한다. |
| **실 인프라 연결 검증은 별도 카드** | 이 환경에는 Prometheus/Loki 가 없다. 코드 경로는 계약 테스트로 검증하고, 실제 연결 확인은 인프라가 주어지면 수행한다 (M1-10 과 같은 처리). |

## 3. 카드

<!-- AUTO:CARDS label=plan:0003 -->
| 카드 | 제목 | 상태 | 담당 | 인계 메모 / 막힌 사유 |
|---|---|---|---|---|
| M2-01 | Prometheus·Loki 어댑터 + 소스 조합(Composite) + 토폴로지 파일 | ✅ done | claude-code | integrations/prometheus.py(DEFAULT_QUERIES: k8s+Micrometer 기준, 파일로 덮어쓰기), loki.py(LogQL, 나노초, X-Scope-OrgID), composite… |
| M2-02 | 이벤트 수집: Alertmanager·CI/CD 웹훅 → DB 이벤트 저장소 | ✅ done | claude-code | integrations/events.py(Alertmanager v4·ChangePayload 파싱, SqlEventStore=EventSource, id 기반 멱등), db OpsEventRow, API /api… |
| M2-03 | 이상 탐지 평가 하네스 + 계절성 탐지기 + 스트리밍 인터페이스 | ✅ done | claude-code | anomaly/synthetic.py(패턴 6종 라벨 생성), evaluation.py(이벤트 단위 P/R/F1·지연·헛알람 묶음), seasonal.py(ACF 주기 추정 + 과거 주기 중앙값 잔차 robust … |
| M2-04 | 로그 템플릿 추출(Drain) + 알람 폭주 압축률 | ✅ done | claude-code | analytics/logs.py(DrainParser: 고정 깊이 트리·유사도 0.5·변수 마스킹, summarize_logs, grouping_accuracy), logs_synthetic.py, events.a… |
| M2-05 | 상황 인식 가중치 학습(로지스틱 회귀) + 토폴로지 영향 전파 | ✅ done | claude-code | analytics/situation_model.py(featurize 8특징, LogisticModel numpy GD+L2+표준화·explain·save/load, assess_learned, propagate_… |
| M2-06 | 변화점 탐지 + 메트릭 상관 + fact sheet 표준 스키마 | ✅ done | claude-code | analytics/changepoint.py(cusum: 클리핑 std 기준선·rewarm·cooldown·relative, CUSUMDetector), factsheet.py(FactSheet·MetricFact… |
| M2-07 | 장애 판정(is_incident) 결정적 평가 | ✅ done | claude-code | DetectionAgent 가 RCAAnalyzer.collect 근거로 규칙·학습 판정+하위 위험 전파를 프롬프트에 주입, LLMAgent.pre_decide 훅으로 트리아지(AIOPS_DETECTION_TRIA… |
| M2-08 | 실 Prometheus·Loki·Alertmanager 연동 검증 | 🟦 ready | - |  |

진행: 7/8
<!-- /AUTO -->

## 4. 완료 판정

- 모든 카드 DoD 체크 + `make check` 통과, 측정값은 각 카드 outputs 에 기록.
- 실 인프라 검증 카드가 해제되면 결과를 §5 에 요약하고 M2 를 종료한다.
