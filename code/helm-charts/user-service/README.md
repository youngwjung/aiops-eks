# user-service Helm Chart

## 사전 조건
- `JWT_SECRET`을 담은 Kubernetes Secret이 대상 네임스페이스에 미리 존재해야 합니다 (order-service, review-service와 공유).
  ```bash
  kubectl -n <namespace> create secret generic saltmart-jwt-secret \
    --from-literal=jwt-secret=<임의의-긴-랜덤-문자열>
  ```
- 이미지가 ECR에 push되어 있어야 합니다.

## 설치
```bash
helm install user-service . \
  --namespace <namespace> \
  --set image.repository=<account_id>.dkr.ecr.<region>.amazonaws.com/user-service \
  --set image.tag=<tag>
```

## 주요 values
| 키 | 기본값 | 설명 |
|---|---|---|
| `replicaCount` | `2` | 파드 개수 |
| `image.repository` | (없음) | ECR 리포지토리 URL, 필수 지정 |
| `image.tag` | `latest` | 이미지 태그 |
| `jwtSecret.secretName` | `saltmart-jwt-secret` | JWT 서명 시크릿을 담은 Secret 이름 |
| `jwtSecret.secretKey` | `jwt-secret` | Secret 내 키 이름 |
| `env.otelExporterOtlpEndpoint` | `http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317` | 트레이스 전송 OTLP gRPC 엔드포인트 |
| `autoscaling.enabled` | `false` | HPA 사용 여부 |
| `serviceMonitor.enabled` | `true` | Prometheus ServiceMonitor 생성 여부 |
| `resources` | requests 100m/128Mi, limits 500m/256Mi | 리소스 요청/제한 |
