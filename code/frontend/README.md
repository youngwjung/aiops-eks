# frontend

소금가게 플랫폼의 웹 화면.

SSR(FastAPI + Jinja2) 방식이라 별도 빌드 과정 없이 바로 실행됩니다. 상품 이미지는 브라우저가 product-service의 내부 클러스터 주소에 직접 접근할 수 없으므로, `/images/{filename}` 라우트가 product-service를 서버사이드로 프록시합니다.

## 로컬 실행
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## 환경변수
| 변수 | 기본값 | 설명 |
|---|---|---|
| `PRODUCT_SERVICE_URL` | `http://localhost:8002` | 상품 목록/상세 조회 |
| `ORDER_SERVICE_URL` | `http://localhost:8003` | 주문 생성/조회 |
| `USER_SERVICE_URL` | `http://localhost:8001` | 로그인/회원가입 |
| `REVIEW_SERVICE_URL` | `http://localhost:8004` | 리뷰 조회/작성 |
| `AI_REVIEW_SUMMARY_ENABLED` | `false` | `true`일 때만 상품 상세 페이지에 AI 리뷰 요약 위젯 노출 |
| `AI_REVIEW_SUMMARY_SERVICE_URL` | (없음) | review-summary-service 엔드포인트. `ENABLED=true`일 때만 사용 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317` | 트레이스를 전송할 OTLP gRPC 엔드포인트 |

`AI_REVIEW_SUMMARY_ENABLED`가 꺼져 있거나(기본값) review-summary-service 엔드포인트가 설정되지 않은 경우, AI 리뷰 요약 위젯 자체가 화면에 나타나지 않습니다. review-summary-service 배포가 완료되어 두 값이 설정되면 프론트엔드 재배포 없이도 다음 요청부터 위젯이 노출됩니다.

인증은 로그인 시 발급받은 JWT를 httponly 쿠키(`access_token`)에 저장하는 방식입니다.