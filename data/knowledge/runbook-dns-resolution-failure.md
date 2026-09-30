# DNS 이름 해석 실패 대응 Runbook

## 증상
- `UnknownHostException`, `NXDOMAIN`, `i/o timeout` (dial tcp: lookup ... on 10.96.0.10:53)
- 서비스 간 호출이 간헐적 또는 전면 실패, 여러 서비스에서 동시에 발생
- CoreDNS 파드 CPU 급증 또는 재시작

## 원인 후보
1. CoreDNS 과부하(ndots:5 설정으로 인한 질의 폭증), 파드 수 부족
2. conntrack 테이블 포화로 UDP 패킷 유실
3. 외부 DNS 업스트림 장애
4. 서비스/네임스페이스 이름 오타, 삭제된 Service

## 조치 절차
1. `kubectl exec ... -- nslookup <svc>` 로 재현, CoreDNS 로그 확인
2. CoreDNS replica 증설, NodeLocal DNSCache 적용 검토
3. 애플리케이션 FQDN 사용(끝에 점) 또는 ndots 조정으로 질의 수 감소
