# product-service

소금가게 플랫폼의 상품 카탈로그 서비스. 상품 조회/등록과 상품 이미지 서빙을 담당합니다.

## 로컬 실행
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8002
```

## 환경변수
| 변수 | 기본값 | 설명 |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317` | 트레이스를 전송할 OTLP gRPC 엔드포인트 |

데이터는 인메모리로 관리되며 재시작 시 초기화됩니다. 기동 시 가전/전자기기 카테고리 상품 12종이 시드 데이터로 생성되고, 상품 이미지는 `static/images/`에서 정적으로 서빙됩니다 (`/static/images/{product_id}.jpg`).