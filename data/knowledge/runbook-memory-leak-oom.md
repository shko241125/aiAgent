# 메모리 누수 · OOM 대응 Runbook

## 증상
- 파드 상태 `OOMKilled`, 재시작 횟수 증가
- memory_usage 가 톱니 모양으로 계속 상승 후 급락(재시작) 반복
- JVM 이면 `java.lang.OutOfMemoryError: Java heap space`, GC 시간 급증 → CPU 동반 상승

## 원인 후보
1. 캐시 크기 제한 누락(무한 증가 Map/List)
2. 커넥션·스트림·리스너 해제 누락
3. 요청 크기 급증(대용량 페이로드)으로 인한 일시적 메모리 부족 — 누수와 구분 필요
4. 컨테이너 메모리 limit 과소 설정

## 조치 절차
1. 재시작 직전 힙 덤프 확보(-XX:+HeapDumpOnOutOfMemoryError) — 재시작만 하면 증거가 사라짐
2. 메모리 증가 시작 시점과 배포 이력 대조 → 배포 직후면 롤백 검토
3. 임시 완화: replica 증설로 파드당 부하 분산, 필요 시 limit 상향
4. 힙 덤프 분석(dominator tree)으로 누수 객체 특정

## 주의
- CPU 급증은 GC 압박의 2차 증상일 수 있다. memory_usage 추세를 먼저 확인할 것.
