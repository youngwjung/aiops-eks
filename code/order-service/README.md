# order-service

소금가게 플랫폼의 주문 처리 서비스. 주문 생성 및 조회를 담당합니다.

## 로컬 실행
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8003
```

## 환경변수
| 변수 | 기본값 | 설명 |
|---|---|---|
| `JWT_SECRET` | `dev-secret-change-me` | user-service와 동일한 값 필요 |
| `PRODUCT_SERVICE_URL` | `http://localhost:8002` | 상품 조회/가격 검증에 사용하는 product-service 엔드포인트 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317` | 트레이스를 전송할 OTLP gRPC 엔드포인트 |

주문 생성 시 product-service를 호출해 상품 존재 여부와 가격을 검증합니다 (서비스 간 호출 발생).