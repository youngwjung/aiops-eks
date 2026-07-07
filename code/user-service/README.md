# user-service

소금가게 플랫폼의 회원 관리 서비스. 회원가입, 로그인, JWT 발급 및 사용자 정보 조회를 담당합니다.

## 로컬 실행
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## 환경변수
| 변수 | 기본값 | 설명 |
|---|---|---|
| `JWT_SECRET` | `dev-secret-change-me` | JWT 서명 시크릿. 다른 서비스와 동일한 값 공유 필요 |
| `JWT_EXPIRE_MINUTES` | `60` | 토큰 만료 시간(분) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317` | 트레이스를 전송할 OTLP gRPC 엔드포인트 |

데이터는 인메모리로 관리되며 재시작 시 초기화됩니다. 기동 시 테스트 계정 3개가 시드 데이터로 생성됩니다 (`alice@saltmart.demo` / `password123` 등).