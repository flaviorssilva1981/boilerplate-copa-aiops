---
prd_number: "002"
status: rascunho
priority: alta
created: 2026-03-22
issue:
depends_on: ["001"]
references: []
---

# PRD 002: Agente de Analise de Causa Raiz e Geracao de Relatorio

## 1. Contexto

- **Sistema/produto**: Agente AIOps para Kubernetes — aplicacao Python com FastAPI, LangChain, Claude Sonnet e MCP Server Kubernetes. O PRD 001 implementa a coleta periodica de eventos Warning do cluster.
- **Estado atual**: Eventos sao coletados automaticamente (PRD 001), mas nao ha analise automatizada. O diagnostico de causa raiz e manual, dependente da experiencia do operador, e nao produz documentacao estruturada. Problemas recorrentes sao reinvestigados do zero.
- **Problema**: O tempo de diagnostico e alto (30min-2h) e varia com a experiencia do operador. Nao ha registro padronizado do processo de troubleshooting, dificultando a transferencia de conhecimento. Eventos ja em tratamento podem ser reprocessados desnecessariamente, desperdicando recursos de API e gerando relatorios duplicados.

## 2. Solucao Proposta

### Visao geral

- Implementa o `EventHandler` do PRD 001, recebendo lista de eventos no contrato de saida definido naquele PRD
- Filtra os eventos que ja estao em tratamento (deduplicacao por `metadata.uid` do evento Kubernetes)
- Agente construido com LangChain + Claude Sonnet usando padrao ReAct (`create_agent`) investiga a causa raiz
- O agente se conecta ao cluster via MCP Server Kubernetes (Flux159) para inspecionar o estado dos recursos, utilizando o pacote `langchain-mcp-adapters` para integracao
- Gera relatorio Markdown estruturado com severidade, causa raiz, evidencias, comandos executados e passos de correcao
- Persiste o relatorio no PostgreSQL com status e referencia aos UIDs dos eventos analisados

### Decisoes-chave

1. **LangChain como framework de agente** — Abstracoes prontas para ReAct, integracao com MCP via pacote `langchain-mcp-adapters` e gerenciamento de tools
2. **Claude Sonnet como LLM** — Equilibrio entre capacidade de raciocinio e custo/velocidade para troubleshooting
3. **MCP Server Kubernetes (Flux159)** — Servidor MCP existente para interacao com o cluster, evitando implementacao propria
4. **Padrao ReAct** — Permite iteracao entre analise e acao ate encontrar a causa raiz real
5. **Deduplicacao por `metadata.uid`** — Eventos ja vinculados a relatorios ativos sao ignorados; eventos vinculados a relatorios com status CORRIGIDO sao reanalisados (possivel reincidencia). Limitacao conhecida: o EventAggregator do Kubernetes pode criar novos objetos Event com UIDs diferentes para o mesmo problema logico em alta frequencia (10+ ocorrencias em 10 minutos). Risco aceito na v1.
6. **Status EM_ANALISE para evitar race condition** — Relatorio e criado com status EM_ANALISE antes de iniciar a investigacao, garantindo que ciclos subsequentes de coleta identifiquem os eventos como "em tratamento" e nao disparem analise duplicada

### Fora do escopo

- **Coleta de eventos** — Responsabilidade do PRD 001
- **Interface de visualizacao dos relatorios** — Responsabilidade do PRD 003
- **Execucao da correcao** — Responsabilidade do PRD 004
- **Historico de eventos brutos no banco** — Apenas relatorios sao persistidos

## 3. Funcionalidades

### US01: Filtragem de eventos ja em tratamento

Como SRE, quero que eventos ja vinculados a um relatorio ativo nao sejam reanalisados, para evitar relatorios duplicados e desperdicio de recursos de API.

**Rules:**
- Antes de disparar a analise, verificar se os `metadata.uid` dos eventos ja estao vinculados a relatorios existentes
- Eventos vinculados a relatorios com status EM_ANALISE, COMPLETO, INCOMPLETO, CORRIGINDO ou FALHA_CORRECAO sao ignorados
- Eventos vinculados a relatorios com status CORRIGIDO sao reanalisados (problema pode ter reincidido)
- A tabela de relatorios armazena o campo `event_uids` (`TEXT[]` com GIN index — lista de UIDs dos eventos Kubernetes analisados)

**Edge cases:**
- Todos os eventos do batch ja estao em tratamento → nao disparar analise, aguardar proximo ciclo
- Parte dos eventos do batch ja esta em tratamento → filtrar e analisar apenas os novos
- Evento reaparece apos relatorio marcado como CORRIGIDO → reanalisar e gerar novo relatorio
- Banco de dados indisponivel para consulta de deduplicacao → registrar erro no log e nao analisar o batch (evita duplicacao); eventos serao reprocessados no proximo ciclo (inferido — validar)

### US02: Analise automatizada de causa raiz

Como SRE, quero que um agente de IA investigue automaticamente a causa raiz dos eventos coletados, para reduzir o tempo de diagnostico e nao depender da experiencia individual de cada operador.

**Rules:**
- O agente e construido com LangChain + Claude Sonnet usando o padrao ReAct (`create_agent` de `langchain.agents`)
- O agente se conecta ao cluster via MCP Server Kubernetes (Flux159), utilizando `langchain-mcp-adapters` para converter tools MCP em tools LangChain
- A investigacao segue o ciclo: analise → acao → reflexao → proxima acao
- O agente itera ate identificar a causa raiz ou atingir o limite de iteracoes, configuravel via variavel de ambiente `AGENT_MAX_ITERATIONS` (padrao: 25)
- O agente deve investigar o estado dos recursos relacionados ao evento (pod, deployment, node, etc.)

**Prompt do agente:**

```
Voce e um analista especializado em diagnostico Kubernetes. Sua funcao e investigar e identificar problemas usando as ferramentas disponiveis.

Voce recebeu os seguintes eventos Warning coletados do cluster:

{events}

## REGRAS
- Apenas diagnostica e sugere correcoes. NUNCA execute kubectl_patch, kubectl_apply ou kubectl_delete.
- Use APENAS os parametros documentados das tools.
- Investigue por PROBLEMA AGRUPADO, nao por evento individual.
- Se ja tiver causa raiz + evidencias suficientes, passe ao proximo problema.
- NUNCA chame a mesma ferramenta com os mesmos parametros duas vezes.

## COMO INVESTIGAR
Voce tem 3 ferramentas de leitura. Use-as IMEDIATAMENTE para coletar evidencias:
- **kubectl_get**: visao geral dos recursos (status, restarts, idade). Use `name` OU `labelSelector`, NUNCA ambos juntos.
- **kubectl_describe**: detalhes completos (eventos, conditions, configuracao).
- **kubectl_logs**: logs do container. Use `previous: true` para logs de execucao anterior.

Para cada problema recebido:
1. Chame kubectl_describe no recurso afetado para obter detalhes
2. Se necessario, chame kubectl_logs para ver erros da aplicacao
3. Com as evidencias coletadas, escreva o relatorio

## TRATAMENTO DE ERROS
- "name cannot be provided when a selector is specified" -> Use APENAS name OU labelSelector
- Se uma ferramenta falhar 2 vezes, registre o erro como evidencia e prossiga

## SEVERIDADE
- **CRITICO**: Pod/Deployment indisponivel, servico fora do ar
- **ALTO**: Restarts frequentes, OOMKilled, recursos esgotados
- **MEDIO**: Warnings recorrentes mas servico funcional
- **BAIXO**: Eventos informativos, problemas cosmeticos

## FORMATO DE RESPOSTA (Markdown)

# Relatorio de Diagnostico Kubernetes

## Resumo
| Total | Criticos | Altos | Medios | Baixos |
|-------|----------|-------|--------|--------|
| X     | X        | X     | X      | X      |

---

## Problema 1: [causa raiz resumida]
- **Severidade:** CRITICO | ALTO | MEDIO | BAIXO
- **Namespace:** namespace-afetado
- **Recursos Afetados:** pod1, deployment/nome

### Causa Raiz
Descricao clara do problema.

### Evidencias
- evidencia 1
- evidencia 2

### Solucao Recomendada
Acao especifica para corrigir.

### Comando Sugerido
kubectl patch deployment nome -n namespace --type=merge -p '{"spec":...}'

Repita a secao para cada problema. Responda APENAS com o relatorio markdown.
```

**Edge cases:**
- MCP Server Kubernetes indisponivel → abortar analise, atualizar relatorio para INCOMPLETO, registrar erro no log; eventos serao reprocessados no proximo ciclo (inferido — validar)
- Agente atinge limite de iteracoes sem identificar causa raiz → gerar relatorio parcial marcando causa raiz como "inconclusiva" com status INCOMPLETO
- Erro na API do Claude (rate limit, timeout) → retry com backoff exponencial; apos 3 tentativas, abortar e registrar erro (inferido — validar)
- Evento ja corrigido entre a coleta e a analise → agente identifica estado saudavel e registra no relatorio
- Variavel de ambiente `AGENT_MAX_ITERATIONS` com valor invalido (nao-numerico, negativo ou zero) → usar o padrao de 25 iteracoes e registrar warning no log

### US03: Geracao e persistencia de relatorio estruturado

Como SRE, quero que cada investigacao produza um relatorio Markdown estruturado e persistido, para ter documentacao padronizada de troubleshooting e facilitar a transferencia de conhecimento.

**Rules:**
- O relatorio e gerado em formato Markdown seguindo a estrutura definida no prompt do agente: resumo com contagem por severidade, e para cada problema: severidade, namespace, recursos afetados, causa raiz, evidencias, solucao recomendada e comando sugerido
- O relatorio e persistido no PostgreSQL com ID unico
- A tabela de relatorios armazena: id, conteudo markdown, status, event_uids (`TEXT[]` — UIDs dos eventos Kubernetes analisados) e timestamps (criacao/atualizacao)
- Status possiveis do relatorio: EM_ANALISE, COMPLETO, INCOMPLETO, CORRIGINDO, CORRIGIDO, FALHA_CORRECAO
- Fluxo de status: EM_ANALISE → COMPLETO (analise OK) | INCOMPLETO (analise inconclusiva); COMPLETO → CORRIGINDO → CORRIGIDO | FALHA_CORRECAO

**Edge cases:**
- PostgreSQL indisponivel no momento da persistencia → retry com backoff; apos falha definitiva, salvar relatorio em arquivo local como fallback e registrar erro (inferido — validar)
- Relatorio gerado com conteudo vazio ou incompleto (falha do LLM) → persistir com status INCOMPLETO para rastreabilidade
- Dois relatorios gerados simultaneamente para eventos relacionados → cada um recebe ID proprio; deduplicacao por `event_uids` impede reanalise nos ciclos futuros

## 4. Visao de Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│                    Pipeline de Analise                    │
│                                                          │
│  Eventos (PRD 001 via EventHandler)                      │
│       │                                                  │
│       ▼                                                  │
│  ┌──────────────────┐    ┌──────────────┐                │
│  │ Filtro de        │───▶│ PostgreSQL   │                │
│  │ Reprocessamento  │◀───│ (event_uids) │                │
│  │ (dedup por UID)  │    └──────────────┘                │
│  └────────┬─────────┘                                    │
│           │ eventos novos                                │
│           ▼                                              │
│  ┌──────────────────────┐    ┌──────────────┐            │
│  │ Criar relatorio      │───▶│ PostgreSQL   │            │
│  │ status: EM_ANALISE   │    │ (relatorios) │            │
│  └────────┬─────────────┘    └──────────────┘            │
│           │                                              │
│           ▼                                              │
│  ┌─────────────────────┐                                 │
│  │  Agente de Analise  │                                 │
│  │ (LangChain + Claude)│                                 │
│  │  Padrao ReAct       │                                 │
│  └────────┬────────────┘                                 │
│           │ MCP (via langchain-mcp-adapters)              │
│           ▼                                              │
│  ┌────────────────┐                                      │
│  │ MCP Server K8s │──▶ Cluster K8s                       │
│  │ (Flux159)      │                                      │
│  └────────────────┘                                      │
│           │                                              │
│           ▼                                              │
│  ┌─────────────────────┐    ┌──────────────┐             │
│  │  Atualizar relatorio │───▶│ PostgreSQL   │             │
│  │  COMPLETO/INCOMPLETO │    │ (relatorios) │             │
│  └─────────────────────┘    └──────────────┘             │
└─────────────────────────────────────────────────────────┘
```

## 5. Criterios de Aceite

### Tecnicos

| Criterio | Metodo de verificacao |
|----------|----------------------|
| Eventos com UID ja vinculado a relatorio ativo sao ignorados | Teste automatizado com eventos duplicados e relatorios em diferentes status |
| Eventos vinculados a relatorio CORRIGIDO sao reanalisados | Teste automatizado simulando reincidencia |
| Relatorio e criado com status EM_ANALISE antes de iniciar investigacao | Teste automatizado validando que ciclo subsequente ignora eventos em analise |
| Agente conecta ao cluster via MCP e executa pelo menos uma iteracao ReAct | Teste de integracao com cluster de teste e evento simulado |
| Relatorio segue a estrutura definida no prompt (resumo, severidade, causa raiz, evidencias, solucao) | Validacao de schema do Markdown via teste automatizado |
| Relatorio e persistido no PostgreSQL com ID unico e event_uids | Teste de integracao com banco de dados |
| Relatorio inconclusivo e persistido com status INCOMPLETO | Teste com cenario onde o agente atinge limite de iteracoes |
| Limite de iteracoes e configuravel via `AGENT_MAX_ITERATIONS` | Teste variando o valor da env var e validando comportamento |

### De negocio

| Metrica | Baseline (fonte) | Meta | Prazo | Min. aceitavel | Responsavel |
|---------|-------------------|------|-------|-----------------|-------------|
| Tempo medio de diagnostico (da recepcao do evento ao relatorio) | 30min-2h manual (estimativa do time de SRE) | < 10 minutos | 30 dias apos deploy | < 20 minutos | Time de SRE |
| Taxa de identificacao correta da causa raiz | N/A — nao ha processo estruturado hoje | > 70% | 30 dias apos deploy | > 50% | Time de SRE |

## 6. Milestones

### Milestone 1: Implementar Filtragem de Reprocessamento

**Objetivo:** Garantir que eventos ja em tratamento nao sejam reanalisados.

**Funcionalidades:** US01, US03 (modelo de dados)

- [ ] Modelar tabela de relatorios no PostgreSQL (id, markdown, status, event_uids `TEXT[]` com GIN index, timestamps) (US03)
- [ ] Implementar consulta de deduplicacao por `event_uids` e status do relatorio (US01)
- [ ] Implementar logica de filtragem antes de disparar analise (US01)
- [ ] Implementar criacao de relatorio com status EM_ANALISE antes de iniciar investigacao (US01, US03)

**Criterio de conclusao:**
- Condicao: Eventos duplicados sao corretamente filtrados com base no status dos relatorios existentes, incluindo EM_ANALISE
- Verificacao: Teste automatizado com cenarios de deduplicacao (evento novo, evento em analise, evento em tratamento, evento corrigido que reincide)
- Aprovador: Time de SRE

### Milestone 2: Implementar Agente de Analise

**Objetivo:** Agente investiga causa raiz automaticamente e gera relatorio estruturado.

**Funcionalidades:** US02, US03

- [ ] Implementar `EventHandler` do PRD 001 como ponto de entrada do pipeline de analise (US02)
- [ ] Configurar LangChain com Claude Sonnet e padrao ReAct (`create_agent` de `langchain.agents`) (US02)
- [ ] Configurar MCP Server Kubernetes (Flux159) como tool do LangChain via `langchain-mcp-adapters` (US02)
- [ ] Implementar logica de investigacao iterativa com limite de iteracoes configuravel via `AGENT_MAX_ITERATIONS` (US02)
- [ ] Gerar relatorio Markdown estruturado seguindo a estrutura definida no prompt (US03)
- [ ] Atualizar relatorio no PostgreSQL com conteudo e status COMPLETO/INCOMPLETO (US03)
- [ ] Implementar retry com backoff para erros de API do Claude (US02)

**Criterio de conclusao:**
- Condicao: Dado um evento Warning, o agente investiga via MCP, identifica causa raiz e atualiza relatorio estruturado no banco
- Verificacao: Teste de integracao end-to-end com cluster de teste e evento Warning simulado
- Aprovador: Time de SRE

## 7. Riscos e Dependencias

| Risco | Impacto | Mitigacao | Status |
|-------|---------|-----------|--------|
| Qualidade da analise do LLM pode variar — causa raiz incorreta | Alto | Monitorar taxa de acerto e iterar nos prompts. Relatorio disponivel para revisao humana | Pendente |
| Custo de API do Claude pode escalar com volume alto de eventos e investigacoes longas | Medio | Monitorar uso de tokens. Limitar iteracoes do agente via `AGENT_MAX_ITERATIONS` | Pendente |
| MCP Server Kubernetes pode nao suportar todas as operacoes necessarias | Medio | Validar operacoes necessarias contra a API do MCP Server antes do desenvolvimento. Operacoes de leitura (kubectl_get, kubectl_describe, kubectl_logs) confirmadas como suportadas | Pendente |
| EventAggregator do Kubernetes cria novos Event objects com UIDs diferentes para o mesmo problema logico em alta frequencia (10+ ocorrencias em 10 min) | Baixo | Risco aceito na v1. Deduplicacao por `metadata.uid` nao colapsara eventos agregados. Mitigacao futura: deduplicar por `involvedObject` + `reason` + `namespace` | Aceito |

**Dependencias:**

| Dependencia | Tipo | Status | Impacto se bloqueado |
|-------------|------|--------|----------------------|
| PRD 001 — Coleta de eventos (contrato de saida e `EventHandler`) | Interna | Em desenvolvimento | Sem eventos coletados, nao ha o que analisar |
| LangChain (`langchain` + `langchain-anthropic` + `langgraph`) | Externa | Disponivel | Framework base do agente |
| `langchain-mcp-adapters` | Externa | Disponivel | Integracao entre LangChain e MCP Server |
| MCP Server Kubernetes (Flux159) | Externa | Disponivel | Sem MCP, agente nao interage com o cluster |
| API Claude (Anthropic) | Externa | Disponivel | Sem LLM, agente nao funciona |
| Instancia PostgreSQL | Interna | A provisionar | Sem banco, relatorios nao sao persistidos |
| Driver PostgreSQL para Python (`asyncpg`) | Externa | Disponivel | Sem driver, aplicacao nao conecta ao banco |

## 8. Referencias

- [PRD 001 - Coleta de Eventos](./001-coleta-eventos-kubernetes.md) — fornece os eventos consumidos por este PRD (contrato de saida e `EventHandler`)
- [PRD 003 - Interface Web](./003-interface-web.md) — consome os relatorios gerados por este PRD
- [PRD 004 - Agente de Correcao](./004-agente-correcao-automatica.md) — consome os relatorios gerados por este PRD
- [MCP Server Kubernetes (Flux159)](https://github.com/Flux159/mcp-server-kubernetes) — servidor MCP utilizado pelo agente
- [LangChain Agents](https://python.langchain.com/docs/how_to/migrate_agent/) — padrao de agente utilizado (`create_agent`)
- [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters) — pacote de integracao LangChain + MCP

## 9. Registro de Decisoes

- **2026-03-22:** Filtragem de reprocessamento fica neste PRD, nao na coleta (PRD 001). Motivo: a decisao depende do estado dos relatorios, que e responsabilidade deste modulo.
- **2026-03-22:** Deduplicacao por `metadata.uid` do evento Kubernetes. Motivo: evitar relatorios duplicados e desperdicio de tokens de API. Limitacao aceita: EventAggregator do K8s pode gerar UIDs diferentes para o mesmo problema logico.
- **2026-03-22:** Eventos vinculados a relatorios CORRIGIDO sao reanalisados. Motivo: problema pode ter reincidido apos correcao.
- **2026-03-22:** Status EM_ANALISE adicionado ao fluxo. Motivo: evitar race condition onde ciclos subsequentes de coleta disparam analise duplicada para eventos ja em investigacao.
- **2026-03-22:** `event_uids` armazenado como `TEXT[]` com GIN index no PostgreSQL. Motivo: consulta de deduplicacao via `ANY()` e a operacao mais frequente; array nativo com GIN e a opcao mais performatica.
- **2026-03-22:** Limite de iteracoes do agente configuravel via `AGENT_MAX_ITERATIONS` (padrao: 25). Motivo: balancear profundidade da investigacao com custo de API e tempo de resposta.
- **2026-03-22:** Prompt do agente documentado no PRD. Motivo: o prompt determina a qualidade da analise e deve ser rastreavel como decisao tecnica.
- **2026-03-22:** `create_agent` de `langchain.agents` escolhido como API de criacao do agente. Motivo: API atual e recomendada do LangChain, substituindo `create_react_agent` depreciado no LangGraph v1.0.
- **2026-03-22:** Integracao MCP via `langchain-mcp-adapters`. Motivo: pacote oficial do langchain-ai para converter tools MCP em tools LangChain. Nao ha integracao MCP nativa no core do LangChain.
- **2026-03-13:** Claude Sonnet escolhido como LLM. Motivo: equilibrio entre capacidade de raciocinio e custo.
- **2026-03-13:** Padrao ReAct para investigacao. Motivo: permite iteracao ate encontrar causa raiz real.
