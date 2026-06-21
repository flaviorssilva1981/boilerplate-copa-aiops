# ──────────────────────────────────────────────────────────────
# generate-github-kubeconfig.ps1
#
# Extracts the github-actions service-account token from OKE
# and builds a self-contained kubeconfig file ready to be
# stored as the GitHub Secret KUBECONFIG_DATA.
#
# Run ONCE after applying k8s/aiops/github-actions-sa.yaml.
# ──────────────────────────────────────────────────────────────
$ErrorActionPreference = "Stop"

<<<<<<< HEAD
$Context   = if ($env:KUBE_CONTEXT) { $env:KUBE_CONTEXT } else { "context-cd2pc3pnoxa" }
$Namespace = "aiops"
$SecretName = "github-actions-token"
$OutFile   = Join-Path $PSScriptRoot "github-kubeconfig-b64.txt"
=======
$Context    = if ($env:KUBE_CONTEXT) { $env:KUBE_CONTEXT } else { "context-cd2pc3pnoxa" }
$Namespace  = "aiops"
$SecretName = "github-actions-token"
$OutFile    = Join-Path $PSScriptRoot "github-kubeconfig-b64.txt"
>>>>>>> b657b8bdb5bbd997e96acdf24ad599bd8f1a2f15

Write-Host "Context : $Context"
Write-Host "Namespace: $Namespace"
Write-Host ""

<<<<<<< HEAD
# ── 1. Wait for the token to be populated (may take a few seconds) ──
=======
# ── 1. Wait for token to be populated (may take a few seconds) ──
>>>>>>> b657b8bdb5bbd997e96acdf24ad599bd8f1a2f15
Write-Host "Waiting for token to be ready..."
$retries = 10
for ($i = 0; $i -lt $retries; $i++) {
    $token = kubectl --context $Context -n $Namespace `
        get secret $SecretName `
        -o jsonpath='{.data.token}' 2>$null
    if ($token) { break }
    Start-Sleep 2
}
<<<<<<< HEAD
if (-not $token) { throw "Token not ready after $retries attempts. Check: kubectl -n $Namespace get secret $SecretName" }

# ── 2. Decode token (base64 → plain JWT) ──────────────────────
=======
if (-not $token) { throw "Token not ready after $retries attempts." }

# ── 2. Decode token (base64 -> plain JWT) ─────────────────────
>>>>>>> b657b8bdb5bbd997e96acdf24ad599bd8f1a2f15
$tokenBytes = [System.Convert]::FromBase64String($token)
$tokenPlain = [System.Text.Encoding]::UTF8.GetString($tokenBytes)

# ── 3. Get CA cert (stays base64 for kubeconfig) ──────────────
$caCert = kubectl --context $Context -n $Namespace `
    get secret $SecretName `
    -o "jsonpath={.data.ca\.crt}"

# ── 4. Get cluster API server URL ─────────────────────────────
$server = kubectl --context $Context `
    config view --minify `
    -o jsonpath='{.clusters[0].cluster.server}'

Write-Host "Server  : $server"
<<<<<<< HEAD
Write-Host "CA cert : $(($caCert).Substring(0,20))..."
Write-Host "Token   : $(($tokenPlain).Substring(0,20))..."
Write-Host ""

# ── 5. Build a minimal, static kubeconfig ─────────────────────
=======
Write-Host ""

# ── 5. Build minimal static kubeconfig ────────────────────────
>>>>>>> b657b8bdb5bbd997e96acdf24ad599bd8f1a2f15
$kubeconfig = @"
apiVersion: v1
kind: Config
clusters:
- name: oke
  cluster:
    server: $server
    certificate-authority-data: $caCert
contexts:
- name: oke
  context:
    cluster: oke
    user: github-actions
    namespace: $Namespace
current-context: oke
users:
- name: github-actions
  user:
    token: $tokenPlain
"@

<<<<<<< HEAD
# ── 6. Base64-encode the kubeconfig ───────────────────────────
=======
# ── 6. Base64-encode ──────────────────────────────────────────
>>>>>>> b657b8bdb5bbd997e96acdf24ad599bd8f1a2f15
$b64 = [Convert]::ToBase64String(
    [System.Text.Encoding]::UTF8.GetBytes($kubeconfig)
)

# ── 7. Write to file ──────────────────────────────────────────
$b64 | Out-File -FilePath $OutFile -Encoding ascii -NoNewline
<<<<<<< HEAD
Write-Host "✓ Written to: $OutFile"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Open https://github.com/flaviorssilva1981/boilerplate-copa-aiops/settings/secrets/actions"
Write-Host "  2. Click 'New repository secret'"
Write-Host "  3. Name:  KUBECONFIG_DATA"
Write-Host "  4. Value: (paste the FULL content of $OutFile)"
Write-Host "  5. Click 'Add secret'"
=======
Write-Host "Written to: $OutFile"
Write-Host ""
Write-Host "Next: paste the content of that file as GitHub Secret KUBECONFIG_DATA"
Write-Host "  https://github.com/flaviorssilva1981/boilerplate-copa-aiops/settings/secrets/actions"
>>>>>>> b657b8bdb5bbd997e96acdf24ad599bd8f1a2f15
