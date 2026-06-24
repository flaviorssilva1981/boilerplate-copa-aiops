"""Manifest catalog for mapping cluster resources to GitOps repo paths."""

_BASE = "kubernetes/argocd_cluster02/applications"
_CORE = f"{_BASE}/core-tools"
_TOOLS = f"{_BASE}/tools"

GITOPS_MANIFEST_CATALOG = f"""
## GitOps manifest catalog (flaviorssilva1981/guiadodevops)

Fixes MUST edit Argo CD Application manifests — never suggest kubectl patch on live resources.
Most apps store Helm values inline under `spec.source.helm.values` in the Application YAML.

| Namespace           | Resource / symptom         | Manifest path |
|---------------------|----------------------------|---------------|
| monitoring          | grafana deployment/probes  | {_CORE}/grafana.yaml |
| monitoring          | prometheus / alertmanager  | {_CORE}/kube-prometheus-stack.yaml |
| monitoring          | loki                       | {_CORE}/loki-stack.yaml |
| tools               | sonarqube probes/resources | {_TOOLS}/sonarqube.yaml |
| ingress-controller  | nginx ingress controller   | {_CORE}/nginx-ingress-controller.yaml |
| trivy-system        | trivy operator scan config | {_CORE}/trivy-operator.yaml |
| security            | vault                      | {_CORE}/vault.yaml |

Helm chart sources under `kubernetes/helm/` can also be edited when the fix targets chart
defaults instead of Argo inline values (e.g. sonarqube chart values.yaml).
"""

PROTECTED_PATH_FRAGMENTS = (
    "sonarqube.yaml",
    "postgresql",
)
