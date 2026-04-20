# FitCopilot Back

Backend do FitCopilot em Flask, estruturado por dominio para atender o workspace operacional do profissional, o painel agregado do aluno, billing integrado ao CORE e o portal simples do aluno com OTP local.

## Stack

- Python 3.12
- Flask
- SQLAlchemy
- PostgreSQL
- Redis
- Celery
- pytest

## Modulos principais

- `app/auth`: autenticacao profissional e sessao local
- `app/accounts`: conta profissional e perfis
- `app/students`: carteira, painel agregado, contexto e portal do aluno
- `app/workouts`: fichas e ativacao de plano
- `app/files`: upload, metadata e fluxo de extracao
- `app/insights`: insights acionaveis
- `app/messaging`: mensagens sugeridas
- `app/reports`: solicitacao e consulta de relatorios
- `app/billing`: planos, assinatura, checkout e portal no padrao CORE
- `app/integrations`: clientes para CORE, email e adaptadores externos
- `app/ai`: provider fake e base para provider local
- `app/jobs`: tarefas assincronas

## Setup local rapido

1. Crie e ative a virtualenv.
2. Instale dependencias com `pip install -r requirements.txt`.
3. Ajuste variaveis em `.env` a partir de `.env.example`.
4. Rode o seed com `python scripts/bootstrap_dev.py`.
5. Suba a API com `python run.py`.

Credenciais seed:

- profissional: `owner@fitcopilot.dev`
- senha: `abcd1234`
- aluno portal: `joao@fitcopilot.dev`

## Testes

- integracao backend: `python -m pytest tests -q`

## Docker

### Backend isolado

```bash
docker build -t fitcopilot-back .
docker run --rm -p 5050:5050 --env-file .env fitcopilot-back
```

### Stack completa

Na raiz `FC/` existe um `docker-compose.yml` que sobe:

- `postgres`
- `redis`
- `back`
- `front`

Suba com:

```bash
docker compose up --build
```

URLs:

- frontend: `http://localhost:3000`
- backend: `http://localhost:5050`

## Jobs

Os jobs estao preparados em `app/jobs/tasks.py` para:

- extracao de arquivo do aluno
- resumo diario do aluno
- geracao de relatorio
- recomputacao de score
- sugestao de mensagem

No ambiente atual o bootstrap e os testes usam execucao simples e provider fake para manter o fluxo funcional.

## CORE

Configuracoes importantes:

- `CORE_API_URL`
- `APP_ID=3`
- `APP_SLUG=fit-copilot`

O backend esta preparado para:

- auth profissional integrada ao CORE
- billing/plans/payments no padrao do `praxis-back`
- envio de OTP do aluno via servico de comunicacao do CORE
- sessao do aluno emitida localmente pelo FitCopilot

## Observacoes

- O projeto hoje usa `db.create_all()` no bootstrap para ambiente dev.
- As migrations ainda precisam ser consolidadas para fluxo formal de deploy.
- O provider de IA atual e fake/local-first, pronto para plug com modelos locais e chave API.
