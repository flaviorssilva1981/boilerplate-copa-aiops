# Deploy the AIOps stack to OKE from Windows PowerShell.
# Usage:
#   $env:ANTHROPIC_API_KEY = "<your-requesty-key>"
#   .\deploy\oke-deploy.ps1

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$KubeConfig = if ($env:KUBECONFIG) { $env:KUBECONFIG } else { "$env:USERPROFILE\.kube\config" }
$Context = if ($env:KUBE_CONTEXT) { $env:KUBE_CONTEXT } else { "context-cd2pc3pnoxa" }
$Namespace = "aiops"

function Invoke-Kubectl {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & kubectl --kubeconfig $KubeConfig --context $Context @Args
    if ($LASTEXITCODE -ne 0) { throw "kubectl failed: $Args" }
}

if (-not $env:ANTHROPIC_API_KEY) {
    throw "Set ANTHROPIC_API_KEY (Requesty key) before running this script."
}

$McpToken = if ($env:MCP_AUTH_TOKEN) { $env:MCP_AUTH_TOKEN } else {
    -join ((48..57 + 97..102) | Get-Random -Count 48 | ForEach-Object { [char]$_ })
}

Write-Host "==> Using kube context: $Context"
Invoke-Kubectl config use-context $Context | Out-Null

Write-Host "==> Applying base manifests"
Invoke-Kubectl apply -f "$RootDir\k8s\aiops\namespace.yaml"
Invoke-Kubectl apply -f "$RootDir\k8s\aiops\rbac.yaml"
Invoke-Kubectl apply -f "$RootDir\k8s\aiops\postgres.yaml"

Write-Host "==> Creating/updating aiops-secrets"
kubectl --kubeconfig $KubeConfig --context $Context -n $Namespace create secret generic aiops-secrets `
    --from-literal=ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY `
    --from-literal=MCP_AUTH_TOKEN=$McpToken `
    --dry-run=client -o yaml | kubectl --kubeconfig $KubeConfig --context $Context apply -f -

$TarPath = Join-Path $env:TEMP "aiops-context.tgz"
if (Test-Path $TarPath) { Remove-Item $TarPath -Force }

Write-Host "==> Packaging application source"
tar -czf $TarPath -C $RootDir `
    --exclude=.git --exclude=.venv --exclude=docs `
    --exclude=k8s --exclude=deploy --exclude=manifestos-k8s .

kubectl --kubeconfig $KubeConfig --context $Context -n $Namespace create configmap aiops-app-source `
    --from-file="context.tgz=$TarPath" `
    --dry-run=client -o yaml | kubectl --kubeconfig $KubeConfig --context $Context apply -f -

Write-Host "==> Deploying MCP server and application"
Invoke-Kubectl apply -f "$RootDir\k8s\aiops\mcp-server.yaml"
Invoke-Kubectl apply -f "$RootDir\k8s\aiops\app.yaml"
Invoke-Kubectl apply -f "$RootDir\k8s\aiops\ingress.yaml"

Write-Host "==> Waiting for rollouts"
Invoke-Kubectl -n $Namespace rollout status deployment/postgres --timeout=300s
Invoke-Kubectl -n $Namespace rollout status deployment/mcp-server-kubernetes --timeout=300s
Invoke-Kubectl -n $Namespace rollout status deployment/aiops-agent-app --timeout=600s

Write-Host ""
Write-Host "Deployment complete."
Write-Host "Ingress URL: https://aiops.dublinconsulting.com.br"
Write-Host "Health:      https://aiops.dublinconsulting.com.br/api/health"
Write-Host "LLM ping:    POST https://aiops.dublinconsulting.com.br/api/agent/ping"
Invoke-Kubectl -n $Namespace get pods,svc,ingress
