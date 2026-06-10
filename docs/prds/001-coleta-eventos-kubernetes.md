---
prd_number: "001"
status: rascunho
priority: alta
created: 2026-03-22
issue:
depends_on: []
references: []
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
- Coleta apenas eventos ocorridos dentro do intervalo do loop (evita reprocessamento de eventos antigos)
- Agrupa os eventos coletados e os disponibiliza para consumo pelo pipeline de análise (PRD 002)

### Decisões-chave

1. **Python SDK para coleta** — Utilizar a biblioteca `kubernetes` do Python ao invés do MCP Server. O MCP é reservado para os agentes de IA; a coleta é uma operação simples que não precisa de abstração de agente.
2. **Loop interno na aplicação** — A coleta roda dentro do processo FastAPI como task assíncrona, sem necessidade de job externo (cron, CronJob K8s).
3. **Intervalo configurável via variável de ambiente** — `EVENT_COLLECTION_INTERVAL_MINUTES` define o período do loop em minutos (padrão: 3 minutos), permitindo ajuste sem rebuild.
4. **Campo `eventTime` para filtro temporal com fallback** — Utilizar o campo `eventTime` da API `events.k8s.io/v1` para filtrar eventos por timestamp. Se `eventTime` for nulo (eventos de controllers legados), usar `deprecatedLastTimestamp` como fallback. Eventos sem nenhum dos dois campos são descartados com warning no log.
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
- Apenas eventos ocorridos dentro do intervalo do loop são coletados (baseado no timestamp do evento)
- Eventos coletados no mesmo intervalo são agrupados antes de serem entregues ao pipeline de análise
- Não há limite de eventos por ciclo de coleta
- Os eventos coletados são transformados em dicts com contrato definido e entregues a uma classe de tratamento (handler) que nesta fase imprime os dados no output; a implementação real será feita no PRD 002

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
        "namespace": str  # namespace do recurso
    },
    "timestamp": str      # ISO 8601 — eventTime ou fallback (deprecatedLastTimestamp)
}
```

**Edge cases:**
- Cluster inacessível durante a coleta → registrar erro no log e aguardar o próximo ciclo sem interromper o loop
- Nenhum evento Warning encontrado no intervalo → não disparar análise, aguardar próximo ciclo
- Volume alto de eventos no mesmo intervalo → sem limite; todos os eventos são coletados e entregues como batch único
- Variável de ambiente `EVENT_COLLECTION_INTERVAL_MINUTES` com valor inválido (não-numérico, negativo ou zero) → usar o padrão de 3 minutos e registrar warning no log
- Permissão insuficiente no cluster (RBAC) para listar eventos → registrar erro claro no output indicando a permissão necessária e continuar o loop (tentar novamente no próximo ciclo)
- Evento com `eventTime` nulo (controller legado) → usar `deprecatedLastTimestamp` como fallback para o campo `timestamp` do contrato de saída
- Evento sem `eventTime` nem `deprecatedLastTimestamp` → descartar o evento e registrar warning no log

**Notas de implementação:**
- A coleta roda como task assíncrona dentro do FastAPI (`asyncio`)
- O filtro por timestamp deve usar o campo `eventTime` da API `events.k8s.io/v1`, com fallback para `deprecatedLastTimestamp` quando `eventTime` for nulo
- Criar uma classe handler (ex: `EventHandler`) com método de processamento que recebe a lista de eventos no formato do contrato de saída. A implementação inicial apenas imprime os dados no output (stdout), sinalizando que o processamento real será implementado no PRD 002

## 4. Critérios de Aceite

### Técnicos

| Critério | Método de verificação |
|----------|----------------------|
| Loop de coleta executa no intervalo configurado | Teste automatizado variando o valor da env var e validando logs |
| Apenas eventos Warning são coletados | Teste com cluster de teste contendo eventos de todos os tipos |
| Coleta abrange todos os namespaces | Teste com eventos em múltiplos namespaces |
| Apenas eventos dentro do intervalo do loop são coletados | Teste com eventos antigos e recentes, validando que apenas recentes são retornados |
| Eventos são agrupados no formato do contrato de saída e entregues ao handler | Teste de integração validando o payload entregue contra o contrato definido |
| Fallback de timestamp funciona para eventos com eventTime nulo | Teste com evento simulado sem eventTime, validando uso de deprecatedLastTimestamp |

### De negócio

| Métrica | Baseline (fonte) | Meta | Prazo | Mín. aceitável | Responsável |
|---------|-------------------|------|-------|-----------------|-------------|
| Tempo entre ocorrência do evento e detecção pelo sistema | 30min-2h manual (estimativa do time de SRE) | ≤ intervalo configurado (3 min padrão) | Na entrega do milestone | ≤ 10 minutos | Time de SRE |

## 5. Milestones

### Milestone 1: Implementar Loop de Coleta

**Objetivo:** Aplicação coleta eventos Warning periodicamente de todos os namespaces.

**Funcionalidades:** US01

- [ ] Configurar autenticação com o cluster via Python SDK Kubernetes (US01)
- [ ] Implementar loop assíncrono com intervalo configurável via `EVENT_COLLECTION_INTERVAL_MINUTES` (US01)
- [ ] Filtrar eventos por tipo (Warning) e por timestamp (dentro do intervalo) com fallback de eventTime → deprecatedLastTimestamp (US01)
- [ ] Implementar transformação dos eventos do SDK para o contrato de saída definido (US01)
- [ ] Criar classe `EventHandler` com implementação placeholder que imprime eventos no output (US01)
- [ ] Agrupar eventos coletados no formato do contrato e entregar ao `EventHandler` (US01)
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

**Dependências:**

| Dependência | Tipo | Status | Impacto se bloqueado |
|-------------|------|--------|----------------------|
| Python SDK Kubernetes (`kubernetes`) | Externa | Disponível | Sem SDK, coleta não funciona |
| Acesso ao cluster Kubernetes com permissões adequadas | Interna | A configurar | Sem acesso, coleta não funciona |

## 7. Referências

- [Python Kubernetes Client](https://github.com/kubernetes-client/python) — SDK utilizado para coleta de eventos
- [PRD 002 - Agente de Análise](./002-agente-analise-causa-raiz.md) — consome os eventos coletados por este PRD
## 8. Registro de Decisões

- **2026-03-22:** Python SDK escolhido para coleta ao invés de MCP. Motivo: coleta é operação simples; MCP é reservado para agentes de IA.
- **2026-03-22:** Filtragem de reprocessamento fica no PRD 002, não na coleta. Motivo: a decisão de quais eventos já foram tratados depende do estado dos relatórios, que é responsabilidade do agente de análise.
- **2026-03-22:** Loop interno na aplicação FastAPI. Motivo: simplicidade, sem necessidade de job externo.
- **2026-03-22:** Intervalo padrão definido em 3 minutos (variável `EVENT_COLLECTION_INTERVAL_MINUTES`).
- **2026-03-22:** Usar campo `eventTime` da API `events.k8s.io/v1` para filtro temporal, com fallback para `deprecatedLastTimestamp` quando `eventTime` for nulo (controllers legados). Eventos sem nenhum timestamp são descartados.
- **2026-03-22:** Sem limite de eventos por ciclo de coleta. Todos são coletados e entregues.
- **2026-03-22:** Erro de RBAC não interrompe o loop; registra no output e tenta novamente no próximo ciclo.
- **2026-03-22:** Classe `EventHandler` criada com implementação placeholder (print no output). Processamento real será implementado no PRD 002.
- **2026-03-22:** Paginação não será implementada neste momento.
- **2026-03-22:** Removido tipo "Error" do escopo — não existe como tipo de evento na API Kubernetes. Apenas Warning é coletado (todos os eventos problemáticos como CrashLoopBackOff, OOMKilled, etc. já são do tipo Warning).
- **2026-03-22:** Definido contrato de saída (dict com campos uid, type, reason, message, namespace, involved_object, timestamp) para desacoplar o handler do SDK Kubernetes.
- **2026-03-22:** Variável de ambiente renomeada de `INTERVAL` para `EVENT_COLLECTION_INTERVAL_MINUTES` para evitar colisão e explicitar a unidade.
- **2026-03-22:** PRD concluído — todas as tarefas do Milestone 1 implementadas. Coleta funcional em `src/aiops_k8s/collector/event_collector.py` com handler placeholder em `event_handler.py`.
