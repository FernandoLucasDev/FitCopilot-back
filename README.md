# FitCopilot Back

Backend Flask do FitCopilot.

Documentacao canonicamente consolidada:

- [README da raiz](/D:/Projects/DreamCore%20Lab/FC/README.md)

Este diretorio contem o dominio principal do produto:

- auth do profissional integrada ao CORE
- billing/plans/payments no padrao do `praxis-back`
- cadastro e painel de alunos
- OTP local do aluno
- fichas e sessoes de treino
- uploads, arquivos e relatorios
- jobs assincronos
- IA com Gemini e fallback local
- integracao de suporte ao WhatsApp

## Stack

- Python 3.12
- Flask
- SQLAlchemy
- Celery
- Redis
- pytest
- google-genai
- boto3
- pypdf

## Modulos principais

- `app/auth`
- `app/students`
- `app/workouts`
- `app/files`
- `app/reports`
- `app/insights`
- `app/messaging`
- `app/billing`
- `app/ai`
- `app/jobs`
- `app/whatsapp`
- `app/common`
- `app/integrations`

## Variaveis importantes

Configuradas em `back/.env`:

- `DATABASE_URL`
- `REDIS_URL`
- `AI_PROVIDER`
- `GEMINI_API_KEY` ou `GEMINI_API_KEY_FILE`
- `STORAGE_PROVIDER`
- `B2_ENDPOINT`
- `B2_BUCKET`
- `B2_KEY_ID`
- `B2_APP_KEY`
- `APP_ID`
- `APP_SLUG`
- `BOT_INTERNAL_SECRET`

## Endpoints principais

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `GET /api/v1/workspace/overview`
- `GET /api/v1/students`
- `POST /api/v1/students`
- `GET /api/v1/students/{id}/panel`
- `POST /api/v1/workouts`
- `POST /api/v1/students/{id}/assign-workout`
- `POST /api/v1/student-auth/request-otp`
- `POST /api/v1/student-auth/verify-otp`
- `POST /api/v1/internal/bot/whatsapp/respond`

## Comandos uteis

Instalacao:

```bash
pip install -r requirements.txt
```

API local:

```bash
python run.py
```

Ou:

```bash
flask --app app:create_app run --host 127.0.0.1 --port 5050
```

Testes:

```bash
python -m pytest tests -q
```

Configurar bucket B2:

```bash
python scripts/configure_b2_bucket.py
```

## Credenciais de desenvolvimento

Instrutor:

- email: `owner@fitcopilot.dev`
- senha: `abcd1234`

Aluno enriquecido para validacao:

- email: `fernando@fitcopilot.dev`

## Estado atual

Ja esta funcionando:

- auth do profissional
- fluxo de aluno por OTP
- ficha de treino estruturada
- sessoes de treino
- upload e resumo de PDF
- B2 real
- Gemini real
- ponte interna para o bot WhatsApp

## Proximos passos

- retries mais fortes para jobs de IA
- worker dedicado no fluxo local
- mais observabilidade
- migracoes e hardening de producao
