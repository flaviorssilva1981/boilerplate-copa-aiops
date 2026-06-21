#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECONFIG_PATH="${KUBECONFIG:-$HOME/.kube/config}"
NAMESPACE="aiops"
CONTEXT="${KUBE_CONTEXT:-context-cd2pc3pnoxa}"

kubectl_cmd() {
  kubectl --kubeconfig "$KUBECONFIG_PATH" --context "$CONTEXT" "$@"
}

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: set ANTHROPIC_API_KEY (Requesty key) before running this script."
  exit 1
fi

MCP_AUTH_TOKEN="${MCP_AUTH_TOKEN:-$(openssl rand -hex 24)}"
CONTEXT_TAR="$(mktemp /tmp/aiops-context.XXXXXX.tgz)"

cleanup() {
  rm -f "$CONTEXT_TAR"
}
trap cleanup EXIT

echo "==> Using kube context: $CONTEXT"
kubectl_cmd config use-context "$CONTEXT" >/dev/null

echo "==> Applying base manifests"
kubectl_cmd apply -f "$ROOT_DIR/k8s/aiops/namespace.yaml"
kubectl_cmd apply -f "$ROOT_DIR/k8s/aiops/rbac.yaml"
kubectl_cmd apply -f "$ROOT_DIR/k8s/aiops/postgres.yaml"

echo "==> Creating/updating aiops-secrets"
kubectl_cmd -n "$NAMESPACE" create secret generic aiops-secrets \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --from-literal=MCP_AUTH_TOKEN="$MCP_AUTH_TOKEN" \
  --dry-run=client -o yaml | kubectl_cmd apply -f -

echo "==> Packaging application source for in-cluster install"
tar -czf "$CONTEXT_TAR" \
  -C "$ROOT_DIR" \
  --exclude='./.git' \
  --exclude='./.venv' \
  --exclude='./docs' \
  --exclude='./k8s' \
  --exclude='./deploy' \
  --exclude='./manifestos-k8s' \
  .

kubectl_cmd -n "$NAMESPACE" create configmap aiops-app-source \
  --from-file=context.tgz="$CONTEXT_TAR" \
  --dry-run=client -o yaml | kubectl_cmd apply -f -

echo "==> Deploying MCP server and application"
kubectl_cmd apply -f "$ROOT_DIR/k8s/aiops/mcp-server.yaml"
kubectl_cmd apply -f "$ROOT_DIR/k8s/aiops/app.yaml"
kubectl_cmd apply -f "$ROOT_DIR/k8s/aiops/ingress.yaml"

echo "==> Waiting for core workloads"
kubectl_cmd -n "$NAMESPACE" rollout status deployment/postgres --timeout=300s
kubectl_cmd -n "$NAMESPACE" rollout status deployment/mcp-server-kubernetes --timeout=300s
kubectl_cmd -n "$NAMESPACE" rollout status deployment/aiops-agent-app --timeout=600s

echo
echo "Deployment complete."
echo "Ingress URL: https://aiops.dublinconsulting.com.br"
echo "Health check: https://aiops.dublinconsulting.com.br/api/health"
echo "LLM ping:     POST https://aiops.dublinconsulting.com.br/api/agent/ping"
echo
kubectl_cmd -n "$NAMESPACE" get pods,svc,ingress
