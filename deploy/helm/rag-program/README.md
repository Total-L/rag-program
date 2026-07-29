# rag-program Helm Chart

部署 `rag-program` RAG API + 配套持久化 / 健康检查 / 可选 HPA / PDB 到 Kubernetes。

## 快速开始(本地 minikube)

```bash
# 1. 安装 chart
helm install rag ./deploy/helm/rag-program \
  --set config.RAG_PROG_API_KEY="$(openssl rand -hex 32)" \
  --set secrets.AUDIT_USER_ID_SALT="$(openssl rand -hex 32)"

# 2. 端口转发 + curl 测试
kubectl port-forward svc/rag-rag-program 8080:80
curl -H "X-API-Key: $KEY" -H "X-Tenant-Id: acme" http://localhost:8080/v1/readyz
```

## 生产最小配置

```yaml
# values-prod.yaml
replicaCount: 3
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: rag.example.com
      paths: [ { path: /, pathType: Prefix } ]
  tls:
    - hosts: [ rag.example.com ]
      secretName: rag-tls
autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
podDisruptionBudget:
  enabled: true
  minAvailable: 2
persistence:
  storageClass: gp3
secrets:
  ANTHROPIC_API_KEY: <base64-encoded>
  AUDIT_USER_ID_SALT: <base64-encoded>
```

```bash
helm upgrade --install rag ./deploy/helm/rag-program \
  -f values-prod.yaml \
  --create-namespace --namespace rag
```

## 安全基线

- `runAsNonRoot: true`, uid=10001(对应 Dockerfile 的 raguser)
- `readOnlyRootFilesystem: true`(/tmp 必须 emptyDir,容器内不能写其他路径)
- `allowPrivilegeEscalation: false`
- 所有 Linux capabilities drop
- ServiceAccount 默认自动创建,无 cluster role

## 升级

`helm upgrade` 时 ConfigMap/Secret 变化会自动触发 deployment rollout
(annotations `checksum/config` + `checksum/secret`)。

## 卸载

```bash
helm uninstall rag
kubectl delete pvc -n rag -l app.kubernetes.io/instance=rag
# 注意:PVC 默认不删,数据保留
```

## Values 关键开关

完整字段见 `values.yaml` 注释。常用：

| 字段 | 默认 | 说明 |
|---|---|---|
| `replicaCount` | 1 | replicas(HPA 开时无效) |
| `ingress.enabled` | false | 是否创建 Ingress |
| `persistence.enabled` | true | PVC audit + data |
| `autoscaling.enabled` | false | HPA |
| `podDisruptionBudget.enabled` | false | PDB |
| `otel.enabled` | false | OpenTelemetry OTLP exporter |
| `securityContext.runAsNonRoot` | true | 非 root |

## 故障排查

```bash
# 看 pod 状态
kubectl get pods -l app.kubernetes.io/name=rag-program
# 看 events
kubectl describe pod -l app.kubernetes.io/name=rag-program
# 看日志(只跑 RAG API 日志,不含 audit)
kubectl logs -l app.kubernetes.io/name=rag-program -c rag-program --tail=100 -f
# 进容器调试
kubectl exec -it <pod> -- /bin/bash
```

更多见 `docs/K8S_DEPLOY.md`。