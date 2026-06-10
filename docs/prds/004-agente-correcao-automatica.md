---
prd_number: "004"
status: rascunho
priority: alta
created: 2026-03-22
issue:
depends_on: ["002", "003"]
references: []
---

# PRD 004: Agente de Correção Automática

## 1. Contexto

- **Sistema/produto**: Agente AIOps para Kubernetes — aplicação Python com FastAPI, LangChain e MCP Server Kubernetes. O PRD 002 gera relatórios com causa raiz e passos de correção. O PRD 003 fornece a interface web com botão para acionar a correção. Stack: Python, FastAPI, LangChain, Claude Sonnet, MCP Server Kubernetes.
- **Estado atual**: Relatórios de análise são gerados com passos de correção recomendados, mas a execução é manual — o operador precisa ler o relatório, interpretar os passos e executar os comandos no cluster manualmente.
- **Problema**: A correção manual adiciona tempo ao MTTR e está sujeita a erros de execução. Mesmo com um bom diagnóstico automatizado, o benefício é parcial se a correção continua dependendo de intervenção humana.

## 2. Solução Proposta

### Visão geral

- Agente construído com LangChain que, quando acionado, lê o relatório de análise e executa os passos de correção no cluster via MCP Server Kubernetes
- Acionado pelo botão "Executar correção" na interface web (PRD 003)
- Após executar a correção, o agente verifica se o problema foi resolvido
- Atualiza o status do relatório no banco (CORRIGINDO → CORRIGIDO ou FALHA_CORRECAO)

### Decisões-chave

1. **Agente separado do agente de análise** — Responsabilidades distintas: um investiga, outro corrige. Permite evolução independente (ex: adicionar proteções na correção sem afetar a análise).
2. **Sem mecanismo de proteção na v1** — O agente tem acesso irrestrito ao cluster via MCP. Não haverá whitelist de ações, dry-run ou restrição por namespace. Risco aceito e documentado.
3. **Verificação pós-correção** — O agente verifica se o problema foi resolvido antes de declarar sucesso. Se o problema persiste, marca como falha.

### Fora do escopo

- **Whitelist de ações permitidas** — Não implementado na v1
- **Dry-run ou preview de ações** — Não implementado na v1
- **Restrição por namespace** — Não implementado na v1
- **Aprovação humana antes da execução** — O clique no botão é a aprovação; não há segundo nível de confirmação
- **Rollback automático** — Se a correção piorar o estado, não há mecanismo de reversão automática na v1

## 3. Funcionalidades

### US01: Execução de correção automática

Como SRE, quero poder acionar a correção automática de um problema a partir do relatório, para reduzir o tempo de resolução sem executar comandos manualmente.

**Rules:**
- O acionamento é feito via endpoint `POST /reports/{id}/fix` na API FastAPI
- O endpoint valida que o relatório existe e tem status COMPLETO; qualquer outro status retorna `409 Conflict`
- O endpoint atualiza o status para CORRIGINDO e retorna `202 Accepted` imediatamente, processando a correção em background (task assíncrona)
- O resultado da correção é consultado pela página de detalhe do relatório (PRD 003), que exibe o status atualizado
- O agente é construído com LangChain e se conecta ao cluster via MCP Server Kubernetes (Flux159)
- O agente lê o relatório de análise (conteúdo Markdown) e extrai os passos de correção
- O agente executa os passos de correção no cluster via MCP
- Na v1, o agente tem acesso irrestrito ao cluster

**Prompt do agente:**

```
Voce e um agente AIOps especializado em correcao de problemas em clusters Kubernetes.
Voce opera no padrao ReAct: para cada acao, primeiro raciocine, depois execute, depois observe o resultado.

Abaixo esta o relatorio de diagnostico com as correcoes recomendadas:

{report_markdown}

## REGRAS
- Execute APENAS as correcoes descritas no relatorio. Nao invente acoes extras.
- NUNCA delete namespaces, PersistentVolumeClaims ou recursos em kube-system.
- NUNCA escale deployments para 0 replicas (a menos que o relatorio peca explicitamente).
- Se um recurso nao existir mais, registre e passe ao proximo passo.
- NUNCA chame a mesma ferramenta com os mesmos parametros duas vezes.
- Se uma ferramenta falhar 2 vezes, registre o erro e passe ao proximo passo.

## COMO CORRIGIR (padrao ReAct)
Para CADA passo de correcao do relatorio, siga este ciclo:

### 1. RACIOCINAR
Antes de agir, responda mentalmente:
- Qual e o estado atual esperado do recurso?
- Qual acao vou executar e por que?
- O que pode dar errado?

### 2. VERIFICAR ESTADO ATUAL
Use kubectl_get ou kubectl_describe para confirmar que o problema ainda existe.
- Se o problema ja foi resolvido, registre como "Ja resolvido" e pule para o proximo.

### 3. EXECUTAR A CORRECAO
Use a ferramenta apropriada (kubectl_patch, kubectl_apply, kubectl_scale, kubectl_rollout, etc.)

### 4. VALIDAR O RESULTADO
Apos executar, use kubectl_get ou kubectl_describe para verificar:
- O recurso esta no estado esperado?
- Pods estao Running/Ready?
- Nao ha novos erros?

Se a validacao falhar, registre o erro e passe ao proximo passo. NAO tente corrigir a correcao.

## FORMATO DE RESPOSTA
IMPORTANTE: A PRIMEIRA LINHA da sua resposta DEVE ser exatamente uma destas (sem markdown, sem emoji, sem formatacao):
- CORRIGIDO: [resumo de uma linha]
- FALHA: [resumo de uma linha]

Depois da primeira linha, use o formato abaixo:

## Acoes Executadas

### Problema 1: [titulo do problema do relatorio]
- **Status:** Corrigido | Falha | Ja resolvido | Recurso nao encontrado
- **Acao:** o que foi feito
- **Validacao:** resultado da verificacao pos-correcao

### Problema 2: ...

## Resumo
- Corrigidos: X
- Falhas: X
- Ja resolvidos: X
- Total: X
```

**Parsing da resposta:** A primeira linha da resposta do agente determina o status do relatório no banco: `CORRIGIDO` → status `CORRIGIDO`; `FALHA` → status `FALHA_CORRECAO`. Se a resposta não seguir o formato esperado (primeira linha não começa com `CORRIGIDO:` nem `FALHA:`), tratar como `FALHA_CORRECAO` e registrar warning no log.

**Edge cases:**
- Passos de correção referenciam recursos que não existem mais → agente registra que o recurso não foi encontrado e continua com os próximos passos
- MCP Server indisponível durante a correção → abortar e atualizar status para FALHA_CORRECAO
- Erro na API do Claude durante a correção → retry com backoff; após 3 tentativas, abortar com FALHA_CORRECAO (inferido — validar)
- Agente de correção já em execução para o mesmo relatório → endpoint rejeita com `409 Conflict` se status é CORRIGINDO (proteção server-side)
- Relatório com status diferente de COMPLETO → endpoint rejeita com `409 Conflict`
- Relatório não encontrado → endpoint retorna `404 Not Found`

### US02: Verificação pós-correção

Como SRE, quero que o agente verifique se o problema foi resolvido após a correção, para ter confiança no resultado sem precisar verificar manualmente.

**Rules:**
- Após executar os passos de correção, o agente verifica o estado dos recursos no cluster via MCP
- Se o problema foi resolvido, o status do relatório é atualizado para CORRIGIDO
- Se o problema persiste, o status é atualizado para FALHA_CORRECAO

**Edge cases:**
- Verificação indica estado ambíguo (parcialmente corrigido) → marcar como FALHA_CORRECAO e detalhar no resultado o que foi corrigido e o que persiste (inferido — validar)
- Recurso demora para estabilizar após correção (ex: pod reiniciando) → aguardar um período configurável antes de verificar (inferido — validar)

## 4. Visão de Arquitetura

```
┌───────────────────────────────────────────────────────┐
│                Pipeline de Correção                    │
│                                                        │
│  Interface Web (PRD 003)                               │
│  [Botão "Executar correção"]                           │
│       │                                                │
│       ▼                                                │
│  ┌──────────────────────┐                              │
│  │ POST /reports/{id}/  │                              │
│  │ fix                  │──▶ 202 Accepted              │
│  │ (validação + dispatch│                              │
│  │  assíncrono)         │                              │
│  └────────┬─────────────┘                              │
│           │ background task                            │
│           ▼                                            │
│  ┌──────────────────────┐                              │
│  │  Agente de Correção  │                              │
│  │ (LangChain + Claude) │                              │
│  └────────┬─────────────┘                              │
│           │                                            │
│     ┌─────┴──────┐                                     │
│     │            │                                     │
│     ▼            ▼                                     │
│  ┌──────────┐  ┌──────────────┐                        │
│  │ Leitura  │  │ Execução     │                        │
│  │ Relatório│  │ via MCP      │──▶ Cluster K8s         │
│  │ (PG)     │  │ (Flux159)    │                        │
│  └──────────┘  └──────┬───────┘                        │
│                       │                                │
│                       ▼                                │
│              ┌─────────────────┐                       │
│              │ Verificação     │                       │
│              │ pós-correção    │──▶ Cluster K8s         │
│              │ (via MCP)       │                       │
│              └────────┬────────┘                       │
│                       │                                │
│              ┌────────┴────────┐                       │
│              │                 │                       │
│              ▼                 ▼                       │
│  ┌────────────────┐  ┌─────────────────┐               │
│  │ Status:        │  │ Status:         │               │
│  │ CORRIGIDO      │  │ FALHA_CORRECAO  │               │
│  └────────────────┘  └─────────────────┘               │
└───────────────────────────────────────────────────────┘
```

## 5. Critérios de Aceite

### Técnicos

| Critério | Método de verificação |
|----------|----------------------|
| Agente lê relatório e executa passos de correção via MCP | Teste de integração com cenário de erro conhecido e correção previsível |
| Status do relatório atualizado corretamente (CORRIGINDO → CORRIGIDO ou FALHA_CORRECAO) | Teste automatizado verificando transições de status |
| Verificação pós-correção detecta se problema persiste | Teste com cenário onde correção não resolve o problema |
| Endpoint `POST /reports/{id}/fix` retorna 202 e processa em background | Teste de integração validando resposta e execução assíncrona |
| Endpoint rejeita com 409 para status diferente de COMPLETO | Teste com relatórios em status CORRIGINDO, EM_ANALISE, FALHA_CORRECAO |
| Endpoint retorna 404 para relatório inexistente | Teste com ID inválido |

### De negócio

| Métrica | Baseline (fonte) | Meta | Prazo | Mín. aceitável | Responsável |
|---------|-------------------|------|-------|-----------------|-------------|
| Taxa de correção automática bem-sucedida | N/A — correção é 100% manual hoje | > 50% | 30 dias após deploy | > 30% | Time de SRE |
| Tempo médio de correção (do acionamento ao resultado) | 15-60min manual (estimativa do time de SRE) | < 5 minutos | 30 dias após deploy | < 15 minutos | Time de SRE |

## 6. Milestones

### Milestone 1: Implementar Agente de Correção

**Objetivo:** Agente executa passos de correção do relatório e verifica resultado.

**Funcionalidades:** US01, US02

- [ ] Implementar endpoint `POST /reports/{id}/fix` com validação de status e dispatch assíncrono (US01)
- [ ] Configurar agente LangChain com MCP Server Kubernetes para correção (US01)
- [ ] Implementar leitura do relatório e extração dos passos de correção (US01)
- [ ] Implementar execução dos passos via MCP (US01)
- [ ] Atualizar status do relatório (CORRIGINDO → CORRIGIDO/FALHA_CORRECAO) (US01, US02)
- [ ] Implementar verificação pós-correção via MCP (US02)
- [ ] Implementar retry com backoff para erros de API (US01)

**Critério de conclusão:**
- Condição: Dado um relatório com passos de correção, o agente executa os passos no cluster e verifica se o problema foi resolvido
- Verificação: Teste de integração end-to-end com cenário de erro conhecido e correção previsível
- Aprovador: Time de SRE

## 7. Riscos e Dependências

| Risco | Impacto | Mitigação | Status |
|-------|---------|-----------|--------|
| Agente com acesso irrestrito pode executar ações destrutivas baseado em análise incorreta | Alto | Aceito na v1. Versões futuras: whitelist, dry-run, restrição por namespace, aprovação humana | Pendente |
| Correção piora o estado do cluster sem mecanismo de rollback | Alto | Relatório disponível para revisão humana antes de acionar correção. Sem rollback automático na v1 | Pendente |
| Custo de API do Claude para o agente de correção | Médio | Monitorar uso de tokens. Correção tende a ser mais curta que análise | Pendente |

**Dependências:**

| Dependência | Tipo | Status | Impacto se bloqueado |
|-------------|------|--------|----------------------|
| PRD 002 — Relatórios com passos de correção | Interna | Em desenvolvimento | Sem relatórios, não há o que corrigir |
| PRD 003 — Interface web (botão de correção) | Interna | Em desenvolvimento | Sem interface, não há como acionar a correção |
| LangChain (`langchain` + `langchain-anthropic`) | Externa | Disponível | Framework base do agente |
| MCP Server Kubernetes (Flux159) | Externa | Disponível | Sem MCP, agente não interage com o cluster |
| API Claude (Anthropic) | Externa | Disponível | Sem LLM, agente não funciona |

## 8. Referências

- [PRD 002 - Agente de Análise](./002-agente-analise-causa-raiz.md) — fornece os relatórios com passos de correção
- [PRD 003 - Interface Web](./003-interface-web.md) — fornece o botão de acionamento da correção
- [MCP Server Kubernetes (Flux159)](https://github.com/Flux159/mcp-server-kubernetes) — servidor MCP utilizado pelo agente

## 9. Registro de Decisões

- **2026-06-09:** Removida a notificação de resultado via Discord do escopo deste PRD. Motivo: simplificação do projeto. Removidas a US03, o Milestone de notificação, a caixa de notificação no diagrama de arquitetura e a dependência de configuração do Discord. O status do relatório no banco (consultável pela interface web) passa a ser a única fonte de verdade do resultado.
- **2026-03-22:** Agente de correção separado do agente de análise. Motivo: responsabilidades distintas; permite evolução independente.
- **2026-03-22:** Prompt do agente de correção documentado no PRD. Motivo: o prompt determina o comportamento do agente e deve ser rastreável como decisão técnica (mesmo padrão do PRD 002).
- **2026-03-22:** Parsing da primeira linha da resposta do agente para determinar status (`CORRIGIDO` → CORRIGIDO, `FALHA` → FALHA_CORRECAO, formato inesperado → FALHA_CORRECAO). Motivo: desacoplar o vocabulário do prompt do schema de status do banco.
- **2026-03-22:** Endpoint `POST /reports/{id}/fix` com execução assíncrona (202 Accepted). Motivo: correção pode demorar; resposta imediata evita timeout na interface web. Proteção contra execução duplicada é server-side (rejeita se status ≠ COMPLETO).
- **2026-03-13:** Sem mecanismo de proteção na v1. Motivo: simplificar primeira entrega, com risco documentado.
- **2026-03-13:** Verificação pós-correção obrigatória. Motivo: garantir confiabilidade do resultado sem verificação manual.
