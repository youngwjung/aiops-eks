# frontend Helm Chart

## 사전 조건
- 이미지가 ECR에 push되어 있어야 합니다.
- user-service, product-service, order-service, review-service가 같은 네임스페이스에 배포되어 있어야 합니다.
- 기존 Envoy Gateway(`eg`, `envoy-gateway-system` 네임스페이스)가 클러스터에 이미 존재해야 합니다 (플랫폼 공통 인프라).

## 설치
```bash
helm install frontend . \
  --namespace <namespace> \
  --set image.repository=<account_id>.dkr.ecr.<region>.amazonaws.com/frontend \
  --set image.tag=<tag> \
  --set ingress.hostname=saltmart.<account_id>.<domain>
```

## 주요 values
| 키 | 기본값 | 설명 |
|---|---|---|
| `replicaCount` | `2` | 파드 개수 |
| `image.repository` | (없음) | ECR 리포지토리 URL, 필수 지정 |
| `image.tag` | `latest` | 이미지 태그 |
| `env.productServiceUrl` 등 | `http://product-service` 등 | 각 백엔드 서비스 엔드포인트 |
| `env.aiReviewSummaryEnabled` | `false` | AI 리뷰 요약 위젯 노출 여부 |
| `env.aiReviewSummaryServiceUrl` | `""` | review-summary-service 엔드포인트 |
| `env.otelExporterOtlpEndpoint` | `http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317` | 트레이스 전송 OTLP gRPC 엔드포인트 |
| `ingress.enabled` | `true` | Envoy Gateway HTTPRoute 생성 여부 |
| `ingress.hostname` | (없음) | 외부 접근 도메인, 필수 지정 |
| `ingress.gatewayName` / `ingress.gatewayNamespace` | `eg` / `envoy-gateway-system` | 참조할 기존 Gateway 리소스 |
| `autoscaling.enabled` | `false` | HPA 사용 여부 |
| `serviceMonitor.enabled` | `true` | Prometheus ServiceMonitor 생성 여부 |
| `resources` | requests 100m/128Mi, limits 500m/256Mi | 리소스 요청/제한 |

review-summary-service가 배포되면 다음과 같이 업그레이드해서 AI 요약 위젯을 켤 수 있습니다:
```bash
helm upgrade frontend . \
  --reuse-values \
  --set env.aiReviewSummaryEnabled=true \
  --set env.aiReviewSummaryServiceUrl=http://review-summary-service
```
