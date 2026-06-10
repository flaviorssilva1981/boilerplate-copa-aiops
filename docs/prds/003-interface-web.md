---
prd_number: "003"
status: rascunho
priority: alta
created: 2026-03-22
issue:
depends_on: ["002"]
references: []
---

# PRD 003: Interface Web de Relatórios

## 1. Contexto

- **Sistema/produto**: Agente AIOps para Kubernetes — aplicação Python com FastAPI. O PRD 002 gera relatórios de análise de causa raiz e os persiste no PostgreSQL. Stack: Python, FastAPI, Jinja2, PostgreSQL.
- **Estado atual**: Os relatórios são gerados e armazenados no banco, mas não há forma de visualizá-los. O operador precisaria consultar o banco diretamente para acessar os resultados da análise.
- **Problema**: Sem interface de visualização, os relatórios não chegam a quem precisa agir. Além disso, não há ponto de acionamento para a correção (PRD 004) — o operador não consegue revisar o diagnóstico e disparar a resolução.

## 2. Solução Proposta

### Visão geral

- Interface web server-side renderizada com Jinja2 pela própria API FastAPI
- Página de listagem com todos os relatórios e seus status
- Página de detalhe com renderização do Markdown completo e botão para acionar correção (PRD 004)

### Decisões-chave

1. **Jinja2 para interface web** — Interface server-side renderizada pela própria API FastAPI, sem necessidade de frontend separado (React, Vue, etc.)
2. **Sem autenticação na v1** — Não haverá controle de acesso. Decisão aceita para simplificar a primeira entrega.

### Fora do escopo

- **Autenticação e autorização** — Não contemplado nesta versão
- **Filtros avançados ou busca na listagem** — Listagem simples sem paginação ou filtro
- **Dashboard com métricas agregadas** — Apenas listagem e detalhe de relatórios
- **Execução da correção** — O botão aciona o agente do PRD 004; a lógica de correção não é responsabilidade deste PRD

## 3. Funcionalidades

### US01: Listagem de relatórios

Como engenheiro de plataforma, quero acessar uma página web com a listagem de todos os relatórios de análise, para ter visibilidade do histórico de problemas investigados no cluster.

**Rules:**
- A página exibe todos os relatórios ordenados por data de criação (mais recente primeiro)
- Cada item da lista exibe: ID, data de criação, status e resumo (primeiras linhas da causa raiz)
- Cada item é clicável e leva à página de detalhe
- A página é servida pela própria API FastAPI com template Jinja2

**Edge cases:**
- Nenhum relatório no banco → exibir página com mensagem informativa ("Nenhum relatório encontrado")
- Erro de conexão com o PostgreSQL → exibir página de erro genérica com mensagem amigável (inferido — validar)

### US02: Visualização detalhada do relatório

Como engenheiro de plataforma, quero visualizar o conteúdo completo de um relatório, para avaliar a causa raiz identificada e os passos de correção recomendados.

**Rules:**
- A página renderiza o conteúdo Markdown completo do relatório em HTML
- Exibe o status atual do relatório
- Inclui botão "Executar correção" que aciona o agente de correção (PRD 004)
- O botão de correção só é exibido para relatórios com status COMPLETO

**Edge cases:**
- Relatório com Markdown malformado → renderizar o que for possível, sem quebrar a página (inferido — validar)
- Relatório não encontrado (ID inválido) → retornar página 404 com mensagem informativa
- Usuário clica em "Executar correção" com correção já em andamento para o mesmo relatório → impedir execução duplicada e informar que a correção está em andamento (inferido — validar)

## 4. Critérios de Aceite

### Técnicos

| Critério | Método de verificação |
|----------|----------------------|
| Interface web lista todos os relatórios com status | Teste manual em navegador |
| Página de detalhe renderiza Markdown completo | Teste manual com relatórios de diferentes complexidades |
| Botão de correção aparece apenas para relatórios COMPLETO | Teste manual com relatórios em diferentes status |

### De negócio

Este PRD é um habilitador de visualização; não possui métrica de negócio própria na v1. O valor é viabilizar o acesso aos relatórios gerados pelo PRD 002 e o acionamento da correção (PRD 004).

## 5. Milestones

### Milestone 1: Implementar Interface Web

**Objetivo:** Permitir visualização dos relatórios via navegador.

**Funcionalidades:** US01, US02

- [ ] Criar template Jinja2 de listagem de relatórios (US01)
- [ ] Criar rota FastAPI para listagem (`GET /reports`) (US01)
- [ ] Criar template Jinja2 de detalhe do relatório com renderização Markdown (US02)
- [ ] Criar rota FastAPI para detalhe (`GET /reports/{id}`) (US02)
- [ ] Adicionar botão "Executar correção" com controle de estado (US02)
- [ ] Tratar cenários de erro (banco indisponível, relatório não encontrado) (US01, US02)

**Critério de conclusão:**
- Condição: Interface web exibe listagem e detalhe dos relatórios corretamente
- Verificação: Demo em navegador com relatórios reais gerados pelo PRD 002
- Aprovador: Time de SRE

## 6. Riscos e Dependências

| Risco | Impacto | Mitigação | Status |
|-------|---------|-----------|--------|
| Interface sem autenticação exposta indevidamente | Médio | Restringir acesso via rede (VPN, ingress interno). Documentar que v1 não tem auth | Pendente |

**Dependências:**

| Dependência | Tipo | Status | Impacto se bloqueado |
|-------------|------|--------|----------------------|
| PRD 002 — Relatórios no PostgreSQL | Interna | Em desenvolvimento | Sem relatórios, não há o que exibir |
| Jinja2 | Externa | Disponível | Renderização dos templates |
| Biblioteca de renderização Markdown → HTML | Externa | A definir | Necessária para exibir relatórios formatados |

## 7. Referências

- [PRD 002 - Agente de Análise](./002-agente-analise-causa-raiz.md) — fornece os relatórios exibidos
- [PRD 004 - Agente de Correção](./004-agente-correcao-automatica.md) — acionado pelo botão de correção na interface

## 8. Registro de Decisões

- **2026-06-09:** Removida a notificação via Discord do escopo deste PRD. Motivo: simplificação do projeto. A interface web passa a ser o único canal de visualização e acionamento da correção. Removidas a US03, o Milestone de notificação, a variável `APP_BASE_URL` e a dependência do bot Discord.
- **2026-03-13:** Jinja2 para interface web. Motivo: server-side renderizada pela própria API, sem frontend separado.
- **2026-03-13:** Sem autenticação na v1. Motivo: simplicidade da primeira entrega.
