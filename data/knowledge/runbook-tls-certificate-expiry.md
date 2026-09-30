# TLS 인증서 만료 대응 Runbook

## 증상
- 클라이언트 에러: `x509: certificate has expired or is not yet valid`, `SSLHandshakeException`, `PKIX path building failed`
- 특정 도메인/게이트웨이만 전면 실패(에러율 100% 에 가까움), 서버 자원 지표는 정상
- 외부 연동(결제·인증 API)에서 핸드셰이크 실패

## 확인
1. `openssl s_client -connect host:443 -servername host | openssl x509 -noout -dates`
2. cert-manager 사용 시 Certificate/CertificateRequest 리소스 상태, ACME 챌린지 실패 여부
3. 중간 인증서(chain) 누락도 같은 증상을 낸다

## 조치 절차
1. 인증서 갱신(cert-manager 재발급 트리거 또는 수동 교체)
2. 게이트웨이/인그레스 재로드, 캐시된 세션 정리
3. 클라이언트 측 트러스트스토어 문제인지 구분 (서버 인증서가 정상이면 클라이언트 쪽)

## 재발 방지
- 만료 30일/7일 전 알람, 자동 갱신 실패 알람
