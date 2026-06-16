---
prd_number: "001"
status: rascunho
priority: alta
created: 2026-06-16
issue:
depends_on: []
references: ["002"]
---

# PRD 001: Coleta Automática de Eventos do Cluster Kubernetes

## 1. Contexto

- **Sistema/produto**: Agente AIOps para Kubernetes — aplicação Python com FastAPI que monitora clusters Kubernetes e automatiza o diagnóstico de problemas. Stack: Python, FastAPI, Python SDK Kubernetes.
- **Estado atual**: A descoberta de eventos problemáticos no cluster é manual. O operador precisa executar `kubectl get events` ou consultar dashboards para identificar que há warnings ou errors. Não existe coleta automatizada nem pipeline que alimente sistemas de análise.
- **Problema**: Sem coleta automática, problemas no cluster passam despercebidos até que um operador investigue manualmente ou um alerta genérico dispare. Isso aumenta o tempo de detecção e, consequentemente, o tempo de indisponibilidade dos serviços.

## 2. Solução Proposta

### Visão geral

- Loop interno na aplicação FastAPI que executa periodicamente a coleta de eventos do cluster
- Utiliza o Python SDK do Kubernetes (`kubernetes`) para listar eventos de todos os namespaces
- Filtra apenas eventos do tipo Warning
- Coleta apenas eventos ocorridos desde a última coleta bem-sucedida (marca d'água) — otimização de janela de query para não reconsultar eventos antigos; a deduplicação por UID (PRD 002) é o que garante não reprocessar
- Agrupa os eventos coletados e os despacha de forma assíncrona (fire-and-forget) ao pipeline de análise (PRD 002), sem aguardar a resposta do agente

### Decisões-chave

1. **Python SDK para coleta** — Utilizar a biblioteca `kubernetes` do Python ao invés do MCP Server. O MCP é reservado para os agentes de IA; a coleta é uma operação simples que não precisa de abstração de agente.
2. **Loop interno + despacho assíncrono fire-and-forget** — A coleta roda dentro do processo FastAPI como task assíncrona, sem job externo (cron, CronJob K8s). Os eventos coletados são despachados ao `EventHandler` de forma assíncrona (fire-and-forget): o coletor não aguarda a resposta do agente e segue para o próximo ciclo.
3. **Intervalo configurável via variável de ambiente** — `EVENT_COLLECTION_INTERVAL_MINUTES` define o período do loop em minutos (padrão: 3 minutos), permitindo ajuste sem rebuild.
4. **Precedência de timestamps para filtro temporal** — Na API `events.k8s.io/v1`, `eventTime` registra a **primeira** observação do evento; recorrências reportadas via EventSeries atualizam apenas `series.lastObservedTime`. A recência de cada evento é determinada pela precedência: `series.lastObservedTime` (quando presente) → `eventTime` → `deprecatedLastTimestamp` (eventos de controllers legados). Eventos sem nenhum dos três campos são descartados com warning no log.
5. **Sem limite de eventos por ciclo** — Todos os eventos Warning do intervalo são coletados e entregues, independente da quantidade.
6. **Classe de tratamento com implementação placeholder** — Criar a classe/interface de tratamento e envio de eventos com uma implementação simples que imprime os dados no output, preparando a estrutura para o PRD 002 implementar o processamento real.

### Fora do escopo

- **Persistência de eventos brutos** — Eventos coletados não são armazenados no banco; são descartados após serem entregues ao pipeline de análise
- **Filtragem inteligente / deduplicação** — A decisão sobre quais eventos já foram tratados é responsabilidade do PRD 002 (Agente de Análise)
- **Coleta de eventos do tipo Normal** — Apenas eventos Warning são coletados; eventos Normal são ignorados
- **Multi-cluster** — Escopo limitado a um cluster por instância da aplicação

## 3. Funcionalidades

### US01: Coleta periódica de eventos Warning

Como engenheiro DevOps, quero que os eventos Warning do cluster sejam coletados automaticamente em intervalos regulares, para não precisar executar kubectl manualmente para descobrir problemas.

**Rules:**
- A coleta deve usar o Python SDK do Kubernetes (`kubernetes`)
- Apenas eventos do tipo Warning são coletados (o tipo "Error" não existe na API de eventos do Kubernetes; problemas como CrashLoopBackOff, OOMKilled e FailedScheduling são reportados como Warning)
- A coleta abrange todos os namespaces do cluster
- O intervalo de coleta é configurável via variável de ambiente `EVENT_COLLECTION_INTERVAL_MINUTES` em minutos (padrão: 3 minutos)
- Apenas eventos ocorridos desde a última coleta bem-sucedida são coletados (marca d'água — otimização de janela de query), com recência determinada pela precedência `series.lastObservedTime` → `eventTime` → `deprecatedLastTimestamp`
- A marca d'água avança a cada coleta bem-sucedida, independentemente do resultado do processamento pelo `EventHandler` (despacho fire-and-forget). A deduplicação por UID (PRD 002) é o único guardião contra reprocessamento; falhas no processamento não seguram a marca d'água — a recuperação se dá por recorrência do evento + recovery de startup/stale do PRD 002
- Eventos coletados no mesmo intervalo são agrupados antes de serem despachados ao pipeline de análise
- Não há limite de eventos por ciclo de coleta
- Os eventos coletados são transformados em dicts com contrato definido e despachados de forma assíncrona (fire-and-forget) a uma classe de tratamento (handler) que nesta fase imprime os dados no output; a implementação real será feita no PRD 002

**Contrato de saída (estrutura de cada evento entregue ao handler):**
```python
{
    "uid": str,           # metadata.uid do evento Kubernetes
    "type": str,          # tipo do evento (ex: "Warning")
    "reason": str,        # reason do evento (ex: "CrashLoopBackOff")
    "message": str,       # mensagem descritiva do evento
    "namespace": str,     # namespace onde o evento ocorreu
    "involved_object": {  # recurso relacionado ao evento
        "kind": str,      # tipo do recurso (ex: "Pod")
        "name": str,      # nome do recurso
        "namespace": str  # namespace do recurso (vazio para recursos cluster-scoped, ex: Node)
    },
    "timestamp": str      # ISO 8601 — series.lastObservedTime, eventTime ou deprecatedLastTimestamp (nesta ordem de precedência)
}
```

O contrato usa nomes próprios, desacoplados do SDK. Mapeamento a partir da API `events.k8s.io/v1`: `note` → `message`, `regarding` → `involved_object` (os nomes `message` e `involvedObject` pertencem à API core/v1 e não existem na API nova).

**Edge cases:**
- Cluster inacessível durante a coleta → registrar erro no log e aguardar o próximo ciclo sem interromper o loop; como nada foi coletado, a marca d'água naturalmente não avança e a janela é reconsultada no próximo ciclo bem-sucedido
- `EventHandler` lança erro ao processar o batch (ex: falha do pipeline de análise do PRD 002 ou banco indisponível) → o handler roda em task assíncrona e trata seus próprios erros internamente (o PRD 002 marca o relatório como INCOMPLETO ou descarta o batch); o erro **não** afeta a marca d'água nem é propagado ao coletor. Eventos one-shot perdidos nesse cenário são risco aceito na v1 (ver Riscos)
- Nenhum evento Warning encontrado no intervalo → não disparar análise, aguardar próximo ciclo
- Volume alto de eventos no mesmo intervalo → sem limite; todos os eventos são coletados e entregues como batch único
- Variável de ambiente `EVENT_COLLECTION_INTERVAL_MINUTES` com valor inválido (não-numérico, negativo ou zero) → usar o padrão de 3 minutos e registrar warning no log
- Permissão insuficiente no cluster (RBAC) para listar eventos → registrar erro claro no output indicando a permissão necessária e continuar o loop (tentar novamente no próximo ciclo)
- Evento recorrente com `series` presente (ex: FailedScheduling contínuo) → usar `series.lastObservedTime` como recência; o evento é coletado mesmo que `eventTime` (primeira observação) seja anterior à marca d'água
- Evento com `eventTime` nulo e sem `series` (controller legado) → usar `deprecatedLastTimestamp` como fallback para recência e para o campo `timestamp` do contrato de saída
- Evento sem `series.lastObservedTime`, `eventTime` nem `deprecatedLastTimestamp` → descartar o evento e registrar warning no log
- Evento de recurso cluster-scoped (ex: Node) → `involved_object.namespace` é entregue como string vazia no contrato

**Notas de implementação:**
- A coleta roda como task assíncrona dentro do FastAPI (`asyncio`)
- O SDK `kubernetes` é síncrono (HTTP bloqueante) — executar as chamadas de listagem via `asyncio.to_thread` para não bloquear o event loop do FastAPI
- Autenticação com o cluster: tentar `config.load_incluster_config()` (execução dentro do cluster) com fallback para `config.load_kube_config()` (execução local via kubeconfig)
- O filtro temporal usa a API `events.k8s.io/v1` com precedência de recência: `series.lastObservedTime` → `eventTime` → `deprecatedLastTimestamp`
- A marca d'água (timestamp da última coleta bem-sucedida) é mantida em memória e avança após cada coleta bem-sucedida, independentemente do processamento pelo `EventHandler` (despacho fire-and-forget). Na primeira execução após o start, a janela inicial é o intervalo configurado. Perder a marca d'água em um restart é inofensivo: a deduplicação por UID (PRD 002) impede reprocessar o overlap recoletado
- Criar uma classe handler (ex: `EventHandler`) com método assíncrono `async def handle(events: list[dict]) -> None` que recebe a lista de eventos no formato do contrato de saída. O handler é invocado como task assíncrona fire-and-forget (ex: `asyncio.create_task`) e **captura suas próprias exceções** (não as propaga ao loop do coletor). A implementação inicial apenas imprime os dados no output (stdout), sinalizando que o processamento real será implementado no PRD 002

## 4. Critérios de Aceite

### Técnicos

| Critério | Método de verificação |
|----------|----------------------|
| Loop de coleta executa no intervalo configurado | Teste automatizado variando o valor da env var e validando logs |
| Apenas eventos Warning são coletados | Teste com cluster de teste contendo eventos de todos os tipos |
| Coleta abrange todos os namespaces | Teste com eventos em múltiplos namespaces |
| Apenas eventos desde a última coleta bem-sucedida (marca d'água) são coletados | Teste com eventos antigos e recentes, validando que apenas os posteriores à marca d'água são retornados |
| Eventos são despachados ao `EventHandler` de forma assíncrona (fire-and-forget), sem o coletor aguardar a resposta | Teste automatizado validando que o ciclo de coleta prossegue sem bloquear na execução do handler |
| Eventos são agrupados no formato do contrato de saída e entregues ao handler | Teste de integração validando o payload entregue contra o contrato definido |
| Precedência de timestamps funciona (series.lastObservedTime → eventTime → deprecatedLastTimestamp) | Testes com evento recorrente (series presente e eventTime antigo), evento sem series e evento legado sem eventTime |

### De negócio

| Métrica | Baseline (fonte) | Meta | Prazo | Mín. aceitável | Responsável |
|---------|-------------------|------|-------|-----------------|-------------|
| Tempo entre ocorrência do evento e detecção pelo sistema | 30min-2h manual (estimativa do time de SRE) | ≤ intervalo configurado (3 min padrão) | Na entrega do milestone | ≤ 10 minutos | Time de SRE |

## 5. Milestones

### Milestone 1: Implementar Loop de Coleta

**Objetivo:** Aplicação coleta eventos Warning periodicamente de todos os namespaces.

**Funcionalidades:** US01

- [ ] Adicionar a dependência `kubernetes` ao projeto (`uv add kubernetes`) (US01)
- [ ] Configurar autenticação com o cluster via `load_incluster_config()` com fallback para `load_kube_config()` (US01)
- [ ] Implementar loop assíncrono com intervalo configurável via `EVENT_COLLECTION_INTERVAL_MINUTES`, executando as chamadas do SDK via `asyncio.to_thread` (US01)
- [ ] Filtrar eventos por tipo (Warning) e por recência desde a marca d'água, com precedência series.lastObservedTime → eventTime → deprecatedLastTimestamp (US01)
- [ ] Implementar transformação dos eventos do SDK para o contrato de saída definido (US01)
- [ ] Criar classe `EventHandler` com método assíncrono `async def handle(events)` e implementação placeholder que imprime eventos no output (US01)
- [ ] Agrupar eventos coletados no formato do contrato e despachar de forma assíncrona (fire-and-forget) ao `EventHandler`, via `asyncio.create_task`, sem aguardar a resposta (US01)
- [ ] Avançar a marca d'água após a coleta bem-sucedida, independentemente do resultado do `EventHandler` (US01)
- [ ] Tratar erros de conectividade e RBAC com logging adequado, sem interromper o loop (US01)

**Critério de conclusão:**
- Condição: A aplicação conecta ao cluster, coleta eventos Warning a cada intervalo configurado e agrupa no formato do contrato de saída para entrega
- Verificação: Teste de integração com cluster de teste e eventos Warning simulados
- Aprovador: Time de SRE

## 6. Riscos e Dependências

| Risco | Impacto | Mitigação | Status |
|-------|---------|-----------|--------|
| Permissões RBAC insuficientes no cluster impedem coleta | Alto | Documentar ClusterRole necessária com permissão de leitura em events de todos os namespaces | Pendente |
| Volume muito alto de eventos pode impactar performance da aplicação | Baixo | Sem limite de eventos por ciclo; paginação não será implementada neste momento. Monitorar em produção | Aceito |
| Evento one-shot que falhe antes de existir relatório (ex: banco fora na dedup do PRD 002) é perdido — a marca d'água já avançou e o evento não recorre | Médio | Aceito na v1. Recuperação só via recorrência do evento + recovery de startup/stale (PRD 002); eventos brutos não são persistidos (decisão de escopo) | Aceito |

**Dependências:**

| Dependência | Tipo | Status | Impacto se bloqueado |
|-------------|------|--------|----------------------|
| Python SDK Kubernetes (`kubernetes`) | Externa | A adicionar (não está no `pyproject.toml`) | Sem SDK, coleta não funciona |
| Acesso ao cluster Kubernetes com permissões adequadas | Interna | A configurar | Sem acesso, coleta não funciona |

## 7. Referências

- [Python Kubernetes Client](https://github.com/kubernetes-client/python) — SDK utilizado para coleta de eventos
- [PRD 002 - Agente de Análise](./002-agente-analise-causa-raiz.md) — consome os eventos coletados por este PRD
## 8. Registro de Decisões
