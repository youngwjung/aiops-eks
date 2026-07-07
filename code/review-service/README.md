# review-service

소금가게 플랫폼의 상품 리뷰 서비스. 리뷰 작성 및 조회를 담당합니다.

## 로컬 실행
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8004
```

## 환경변수
| 변수 | 기본값 | 설명 |
|---|---|---|
| `JWT_SECRET` | `dev-secret-change-me` | user-service와 동일한 값 필요 |
| `PRODUCT_SERVICE_URL` | `http://localhost:8002` | 리뷰 작성 시 상품 존재 검증에 사용 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317` | 트레이스를 전송할 OTLP gRPC 엔드포인트 |

데이터는 인메모리로 관리되며 재시작 시 초기화됩니다. 기동 시 일부 상품에 초기 리뷰가 시드되며, 나머지 상품은 아직 리뷰가 등록되지 않은 상태로 시작합니다.

다른 서비스가 `GET /api/reviews?product_id=` 로 이 서비스를 호출해 특정 상품의 리뷰를 조회할 수 있습니다.