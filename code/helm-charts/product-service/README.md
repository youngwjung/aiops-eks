# product-service Helm Chart

## 사전 조건
- 이미지가 ECR에 push되어 있어야 합니다.

## 설치
```bash
helm install product-service . \
  --namespace <namespace> \
  --set image.repository=<account_id>.dkr.ecr.<region>.amazonaws.com/product-service \
  --set image.tag=<tag>
```

## 주요 values
| 키 | 기본값 | 설명 |
|---|---|---|
| `replicaCount` | `2` | 파드 개수 |
| `image.repository` | (없음) | ECR 리포지토리 URL, 필수 지정 |
| `image.tag` | `latest` | 이미지 태그 |
| `env.otelExporterOtlpEndpoint` | `http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317` | 트레이스 전송 OTLP gRPC 엔드포인트 |
| `autoscaling.enabled` | `false` | HPA 사용 여부 |
| `serviceMonitor.enabled` | `true` | Prometheus ServiceMonitor 생성 여부 |
| `resources` | requests 100m/128Mi, limits 500m/256Mi | 리소스 요청/제한 |
