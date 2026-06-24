"""Manifest catalog for mapping cluster resources to GitOps repo paths."""

GITOPS_MANIFEST_CATALOG = """
## GitOps manifest catalog (flaviorssilva1981/guiadodevops)

Fixes MUST edit Argo CD Application manifests — never suggest kubectl patch on live resources.
Most apps store Helm values inline under `spec.source.helm.values` in the Application YAML.

| Namespace           | Resource / symptom              | Manifest path |
|---------------------|---------------------------------|---------------|
| monitoring          | grafana deployment/probes       | kubernetes/argocd_cluster02/applications/core-tools/grafana.yaml |
| monitoring          | prometheus / alertmanager       | kubernetes/argocd_cluster02/applications/core-tools/kube-prometheus-stack.yaml |
| monitoring          | loki                            | kubernetes/argocd_cluster02/applications/core-tools/loki-stack.yaml |
| tools               | sonarqube probes/resources      | kubernetes/argocd_cluster02/applications/tools/sonarqube.yaml |
| ingress-controller  | nginx ingress controller        | kubernetes/argocd_cluster02/applications/core-tools/nginx-ingress-controller.yaml |
| trivy-system        | trivy operator scan config      | kubernetes/argocd_cluster02/applications/core-tools/trivy-operator.yaml |
| security            | vault                           | kubernetes/argocd_cluster02/applications/core-tools/vault.yaml |

Helm chart sources under `kubernetes/helm/` can also be edited when the fix targets chart defaults
instead of Argo inline values (e.g. sonarqube chart values.yaml).
"""

PROTECTED_PATH_FRAGMENTS = (
    "sonarqube.yaml",
    "postgresql",
)
