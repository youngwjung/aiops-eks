# review-summary-service

소금가게 플랫폼의 AI 리뷰 요약 서비스. review-service에서 특정 상품의 리뷰를 모아 Bedrock(Claude)으로 장점/단점/총평을 요약해 반환합니다.

## 로컬 실행
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## 환경변수
| 변수 | 기본값 | 설명 |
|---|---|---|
| `REVIEW_SERVICE_URL` | `http://localhost:8004` | 리뷰 조회에 사용하는 review-service 엔드포인트 |
| `BEDROCK_MODEL_ID` | `anthropic.claude-3-5-haiku-20241022-v1:0` | 요약 생성에 사용할 Bedrock 모델 ID |
| `AWS_REGION` | `us-west-2` | Bedrock 호출 리전 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317` | 트레이스를 전송할 OTLP gRPC 엔드포인트 |

별도 DB 없이 완전 무상태로 동작하며, 매 요청마다 review-service 조회와 Bedrock 호출을 새로 수행합니다. 
Bedrock 호출 권한(`bedrock:InvokeModel`)이 있어야 정상 동작합니다.