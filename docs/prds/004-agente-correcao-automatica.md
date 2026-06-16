---
prd_number: "004"
status: rascunho
priority: alta
created: 2026-06-16
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
- O endpoint valida que o relatório existe e tem status COMPLETO ou FALHA_CORRECAO (nova tentativa após falha de correção); qualquer outro status retorna `409 Conflict`
- A transição para CORRIGINDO é feita por UPDATE condicional atômico (`UPDATE reports SET status='CORRIGINDO' WHERE id=:id AND status IN ('COMPLETO','FALHA_CORRECAO') RETURNING id`); se nenhuma linha for atualizada, o endpoint retorna `409 Conflict` — isso evita que duas requisições concorrentes disparem dois agentes de correção para o mesmo relatório (corrida TOCTOU)
- Atualizado o status para CORRIGINDO, o endpoint retorna `202 Accepted` imediatamente e processa a correção em background via `BackgroundTasks` do FastAPI (sem fila externa); o trabalho roda in-process e é perdido em caso de restart — daí a recuperação de startup descrita abaixo
- O resultado da correção é consultado pela página de detalhe do relatório (PRD 003), que exibe o status atualizado e o **feedback da correção** (resposta do agente: Ações Executadas + validação) persistido na coluna `correction_result`, numa seção "Resultado da Correção"
- O agente é construído com LangChain e se conecta ao cluster via MCP Server Kubernetes (Flux159), usando `langchain-mcp-adapters` para conectar por HTTP (`streamable_http`) ao MCP executado localmente via `npx` (transport HTTP/streamable) em `MCP_SERVER_URL` (padrão `http://localhost:3001/mcp`) — mesmo padrão do PRD 002
- O modelo é configurável via variável de ambiente `AGENT_MODEL_NAME` (padrão: `claude-haiku-4-5`), reutilizando a mesma env var do PRD 002
- Ao contrário do agente de análise, todas as tools do MCP Server são expostas ao agente de correção (acesso irrestrito, conforme decisão-chave 2)
- O número máximo de iterações é configurável via `CORRECTION_MAX_ITERATIONS` (padrão: 25), implementado com `ModelCallLimitMiddleware(run_limit, exit_behavior="end")`; ao atingir o limite o run termina graciosamente (sem nova chamada ao modelo) e a resposta final não segue o formato `CORRIGIDO:/FALHA:`, de modo que o parsing da primeira linha (ver abaixo) a classifica como FALHA_CORRECAO — não há detecção separada de "limite atingido"
- O agente lê o relatório de análise (conteúdo Markdown) e extrai a correção recomendada das seções **Solução Recomendada** e **Comando Sugerido** do relatório (PRD 002), traduzindo o comando sugerido em chamadas de tool MCP estruturadas (ex: `kubectl_patch`, `kubectl_scale`)
- O agente executa os passos de correção no cluster via MCP
- Na v1, o agente tem acesso irrestrito ao cluster
- Relatórios órfãos em CORRIGINDO encontrados no startup da aplicação são marcados como FALHA_CORRECAO (recuperação de correções interrompidas por crash/restart); como FALHA_CORRECAO é um status re-acionável, a correção pode ser disparada novamente

**Prompt do agente:**

```
Voce e um agente AIOps especializado em correcao de problemas em clusters Kubernetes.
Voce opera no padrao ReAct: para cada acao, primeiro raciocine, depois execute, depois observe o resultado.

Abaixo esta o relatorio de diagnostico com as correcoes recomendadas:

{report_markdown}

## REGRAS
- Execute as correcoes descritas no relatorio. NAO corrija problemas pre-existentes sem relacao com o relatorio (esses sim estao fora de escopo).
- POREM, se a SUA propria correcao introduzir um novo erro ou deixar o recurso pior, isso NAO esta fora de escopo: foi voce que causou e voce DEVE corrigir ate o recurso ficar saudavel.
- Ao editar um container existente (image, env, resources, probes) com kubectl_patch, use patchType "strategic" OU kubectl_apply. NUNCA use merge patch simples sobre a lista `containers`: o merge substitui o container inteiro e APAGA os campos nao citados (env, image, probes, ports).
- NUNCA delete namespaces, PersistentVolumeClaims ou recursos em kube-system.
- NUNCA escale deployments para 0 replicas (a menos que o relatorio peca explicitamente).
- Se um recurso nao existir mais, registre e passe ao proximo passo.
- NUNCA repita a mesma chamada de ferramenta com os mesmos parametros: se falhar, leia a mensagem de erro e tente uma abordagem diferente.
- Se o mesmo passo falhar 2 vezes mesmo com abordagens diferentes, registre o erro e siga adiante.

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
Use a ferramenta apropriada (kubectl_patch, kubectl_apply, kubectl_scale, kubectl_rollout, etc.).
Ao alterar um container, prefira patchType "strategic" ou kubectl_apply para PRESERVAR os demais campos (env, image, probes, ports). Nunca remova variaveis de ambiente, a imagem ou probes que ja existiam, a menos que o relatorio peca.

### 4. VALIDAR O RESULTADO
Apos executar, use kubectl_get ou kubectl_describe para verificar de fato a SAUDE do recurso afetado:
- O recurso esta no estado esperado?
- O pod esta Running E Ready (sem CrashLoopBackOff, sem restarts subindo, sem novos erros em eventos/logs)?
- A sua correcao NAO introduziu nenhum erro novo?

Se a validacao mostrar que a SUA correcao causou um novo erro ou deixou o pod quebrado (ex.: saiu de ImagePullBackOff mas entrou em CrashLoop porque o patch apagou variaveis de ambiente), NAO trate isso como "fora de escopo": foi a sua acao que causou, entao CORRIJA. Volte ao ciclo (raciocine -> verifique -> execute -> valide) ate o recurso ficar saudavel, respeitando o limite de iteracoes.

So considere o problema CORRIGIDO quando o recurso afetado estiver comprovadamente saudavel (pod Running e Ready, Deployment Available, sem novos erros). Se, depois das suas tentativas, o recurso continuar quebrado, responda FALHA explicando o que ficou pendente.

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

> Nota: como cada relatório corresponde a um único problema (decisão do PRD 002), na prática a resposta terá apenas "Problema 1"; o formato suporta múltiplos problemas por robustez.

**Parsing da resposta:** O **veredito** da resposta do agente determina o status do relatório no banco: uma linha que comece com `CORRIGIDO:` → status `CORRIGIDO`; com `FALHA:` → status `FALHA_CORRECAO`. O parser localiza o veredito em **qualquer linha** da resposta (não apenas a primeira), pois o agente costuma escrever um resumo em prosa antes do veredito; o token deve ancorar no início de uma linha (tolerando marcação Markdown como `-`, `*`, `#`, `>`) para não casar com menções no meio de frase. Se nenhum veredito for encontrado, tratar como `FALHA_CORRECAO` e registrar warning no log.

**Edge cases:**
- Passos de correção referenciam recursos que não existem mais → agente registra que o recurso não foi encontrado e continua com os próximos passos
- MCP Server indisponível durante a correção → abortar e atualizar status para FALHA_CORRECAO
- Erro na API do Claude durante a correção → `max_retries=3` no `ChatAnthropic` aplica backoff exponencial automaticamente (mesmo mecanismo do PRD 002); após 3 tentativas, abortar e atualizar status para FALHA_CORRECAO
- Agente de correção já em execução para o mesmo relatório → endpoint rejeita com `409 Conflict` se status é CORRIGINDO (proteção server-side via UPDATE condicional atômico)
- Relatório com status diferente de COMPLETO ou FALHA_CORRECAO → endpoint rejeita com `409 Conflict`
- Aplicação reinicia durante a correção → relatório órfão em CORRIGINDO é marcado como FALHA_CORRECAO no startup, permitindo nova tentativa pelo botão da interface (PRD 003)
- Relatório não encontrado → endpoint retorna `404 Not Found`

### US02: Verificação pós-correção

Como SRE, quero que o agente verifique se o problema foi resolvido após a correção, para ter confiança no resultado sem precisar verificar manualmente.

**Rules:**
- Após executar os passos de correção, o agente verifica o estado dos recursos no cluster via MCP
- O status só é CORRIGIDO se o recurso afetado ficar comprovadamente saudável (pod Running e Ready, Deployment Available, sem novos erros); caso contrário, FALHA_CORRECAO
- Se a própria correção introduzir um novo erro (ex.: um patch destrutivo que apaga variáveis de ambiente), o agente deve corrigi-lo — e não tratá-lo como "problema separado fora de escopo"
- Se o problema persiste, o status é atualizado para FALHA_CORRECAO

**Edge cases:**
- Verificação indica estado ambíguo (parcialmente corrigido) → já resolvido pelo mecanismo de parsing: qualquer problema com falha faz o agente responder `FALHA:` → status `FALHA_CORRECAO`; o corpo da resposta (seção "Ações Executadas") detalha o que foi corrigido e o que falhou
- Recurso demora para estabilizar após correção (ex: pod reiniciando) → limitação conhecida na v1: o agente verifica o estado imediatamente após cada ação, sem aguardar estabilização; pods em inicialização podem gerar falso FALHA_CORRECAO

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
| Endpoint rejeita com 409 para status diferente de COMPLETO/FALHA_CORRECAO | Teste com relatórios em status CORRIGINDO, EM_ANALISE, INCOMPLETO, CORRIGIDO |
| Endpoint aceita FALHA_CORRECAO e reinicia a correção | Teste acionando `/fix` em relatório FALHA_CORRECAO e validando transição para CORRIGINDO |
| Transição COMPLETO/FALHA_CORRECAO → CORRIGINDO é atômica (rejeita concorrência) | Teste de concorrência: duas requisições simultâneas, apenas uma dispara a correção |
| Relatório CORRIGINDO órfão é marcado FALHA_CORRECAO no startup | Teste simulando restart com relatório em CORRIGINDO |
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

- [ ] Implementar endpoint `POST /reports/{id}/fix` aceitando status COMPLETO ou FALHA_CORRECAO, com transição atômica para CORRIGINDO (UPDATE condicional) e dispatch assíncrono via `BackgroundTasks` (US01)
- [ ] Configurar agente LangChain com `AGENT_MODEL_NAME` (padrão `claude-haiku-4-5`) e MCP Server Kubernetes via `langchain-mcp-adapters` (MCP via `npx` com transport HTTP/streamable, conexão `streamable_http` em `MCP_SERVER_URL`), expondo todas as tools do MCP (acesso irrestrito) (US01)
- [ ] Implementar limite de iterações via `CORRECTION_MAX_ITERATIONS` (padrão: 25) com `ModelCallLimitMiddleware(run_limit, exit_behavior="end")`; ao atingir o limite, a classificação FALHA_CORRECAO vem do fallback de parsing da primeira linha (sem detecção separada) (US01)
- [ ] Implementar leitura do relatório e extração dos passos de correção (US01)
- [ ] Implementar execução dos passos via MCP (US01)
- [ ] Atualizar status do relatório (CORRIGINDO → CORRIGIDO/FALHA_CORRECAO) (US01, US02)
- [ ] Implementar recuperação de startup: marcar relatórios CORRIGINDO órfãos como FALHA_CORRECAO (US01)
- [ ] Implementar verificação pós-correção via MCP (US02)
- [ ] Configurar `max_retries=3` no `ChatAnthropic` para retry com backoff exponencial nativo do SDK (US01)

**Critério de conclusão:**
- Condição: Dado um relatório com passos de correção, o agente executa os passos no cluster e verifica se o problema foi resolvido
- Verificação: Teste de integração end-to-end com cenário de erro conhecido e correção previsível
- Aprovador: Time de SRE

## 7. Riscos e Dependências

| Risco | Impacto | Mitigação | Status |
|-------|---------|-----------|--------|
| Agente com acesso irrestrito pode executar ações destrutivas baseado em análise incorreta | Alto | Aceito na v1. Versões futuras: whitelist, dry-run, restrição por namespace, aprovação humana | Pendente |
| Correção piora o estado do cluster sem mecanismo de rollback | Alto | Relatório disponível para revisão humana antes de acionar correção. Sem rollback automático na v1; após FALHA_CORRECAO a correção pode ser re-acionada manualmente (mitigação parcial) | Pendente |
| Custo de API do Claude para o agente de correção | Médio | Monitorar uso de tokens. Correção tende a ser mais curta que análise | Pendente |

**Dependências:**

| Dependência | Tipo | Status | Impacto se bloqueado |
|-------------|------|--------|----------------------|
| PRD 002 — Relatórios com passos de correção | Interna | Em desenvolvimento | Sem relatórios, não há o que corrigir |
| PRD 003 — Interface web (botão de correção) | Interna | Em desenvolvimento | Sem interface, não há como acionar a correção |
| LangChain (`langchain` + `langchain-anthropic`) | Externa | Disponível | Framework base do agente |
| `langchain-mcp-adapters` | Externa | Disponível — `langchain-mcp-adapters>=0.1.0` em `pyproject.toml` | Bridge entre LangChain e MCP Server Kubernetes |
| MCP Server Kubernetes (Flux159) | Externa | Disponível | Sem MCP, agente não interage com o cluster |
| Node.js + npx (runtime do MCP Server Kubernetes, executado localmente com transport HTTP) | Externa | A provisionar | Sem o runtime Node, o MCP server não inicia |
| API Claude (Anthropic) | Externa | Disponível | Sem LLM, agente não funciona |

## 8. Referências

- [PRD 002 - Agente de Análise](./002-agente-analise-causa-raiz.md) — fornece os relatórios com passos de correção
- [PRD 003 - Interface Web](./003-interface-web.md) — fornece o botão de acionamento da correção
- [MCP Server Kubernetes (Flux159)](https://github.com/Flux159/mcp-server-kubernetes) — servidor MCP utilizado pelo agente

## 9. Registro de Decisões
