# DB 커넥션 풀 고갈 대응 Runbook

## 증상
- 애플리케이션 로그에 `HikariPool-1 - Connection is not available, request timed out` 에러 급증
- API latency_p95_ms 급증, error_rate 상승 (주로 5xx)
- DB 자체의 CPU 는 정상인 경우가 많음

## 주요 원인
1. 신규 배포에서 트랜잭션을 닫지 않는 코드(커넥션 누수)
2. 슬로우 쿼리로 인한 커넥션 점유 시간 증가
3. 트래픽 급증 대비 maximumPoolSize 과소 설정

## 조치 절차
1. 최근 배포 이력 확인 — 배포 직후 발생했다면 롤백을 최우선 검토
2. DB 의 active session / slow query 확인
3. 임시 완화: 서비스 롤링 재시작으로 누수된 커넥션 회수
4. 트래픽 급증이 원인이면 replica 스케일 아웃

## 재발 방지
- 커넥션 누수 탐지(leakDetectionThreshold) 설정
- 배포 전 부하 테스트에 커넥션 풀 지표 포함
