# Rag-Email-Agent

A RAG-based email agent for Outlook — indexes email content for retrieval
and (eventually) sends a daily digest summarizing and triaging each day's
messages by urgency. Supports multiple Outlook accounts, gated by an
allowlist, so I can use it across my own accounts and eventually share it
with a small group of others.

See [CLAUDE.md](CLAUDE.md) for the full build order and current status, and
[PLANNING.md](PLANNING.md) for the design-decision log.

## Stack

- Python, Flask
- Postgres + `pgvector` for embedding storage/similarity search (local Docker container)
- Microsoft Graph API via MSAL (`Mail.Read` + `Mail.Send` scopes, personal Microsoft accounts)
- OpenAI API (`text-embedding-3-small` for embeddings, `gpt-5-mini` for summarization/triage)

## Status

- ✅ Step 1 — OAuth + basic fetch
- ✅ Step 2 — Chunk + embed emails into Postgres/pgvector
- ✅ Step 3 — Retrieval + summarization/triage
- ✅ Step 4 — Daily digest dispatch
- ⏳ Step 5 — Automation + guardrails (not started)
- ✅ Multi-user OAuth + allowlist (encrypted per-account token storage)

## Setup

The steps below are for **running your own deployment** of this project
(what I do for local development, and what a cloud deployment would
require). End users of a running deployment don't do any of this — they
just authenticate via Microsoft OAuth and, if their email is on the
allowlist, their account gets provisioned automatically. See PLANNING.md's
multi-user decision for details.

### 1. Azure AD app registration

Register an app at [portal.azure.com](https://portal.azure.com) → Microsoft
Entra ID → App registrations. Details (account type, redirect URI, API
permissions) are documented in [PLANNING.md](PLANNING.md) under Step 1 —
follow those exactly, since the auth flow depends on the registration
matching `config.py`/`.env`.

### 2. OpenAI API key

Get an API key from [platform.openai.com](https://platform.openai.com) —
used for chunk embeddings (`text-embedding-3-small`) and summarization/
triage (`gpt-5-mini`). Cost is negligible at this project's volume.

### 3. Postgres (Docker)

```bash
docker compose up -d
```

This starts a `pgvector/pgvector` Postgres container and runs
`db/init/001_schema.sql` on first boot to create the schema. If the schema
changes after your container already exists, apply the relevant file(s) in
`db/migrations/` by hand (see PLANNING.md for the pattern) — `db/init/`
only runs against a fresh, empty volume.

### 4. Python environment

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows; use .venv\Scripts\Activate.ps1 for PowerShell
pip install -r requirements.txt
```

### 5. Configure `.env`

```bash
cp .env.example .env
```

Fill in `CLIENT_ID` (from the Azure app registration), a random
`FLASK_SECRET_KEY`, `OPENAI_API_KEY`, a `TOKEN_ENCRYPTION_KEY` (generate
with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
— **temporary**: lives in `.env` for local dev only, must move to AWS
Secrets Manager before actual Lambda deployment), and Postgres credentials
(these initialize the Docker container, so pick values before first
running `docker compose up`, or run `docker compose down -v` to reinit if
you change them after — this wipes local dev data). See `.env.example` for
the full list with comments.

### 6. Approve at least one account

No admin UI — add yourself (and anyone else) to the allowlist directly:

```sql
INSERT INTO approved_users (email) VALUES ('you@outlook.com');
```

## Running

**Authenticate:**

```bash
python app.py
```

Visit `http://localhost:5000/auth/login` and sign in with an approved
Microsoft account. On success, your account's OAuth refresh token is
encrypted and stored in the `accounts` table — this is what every command
below uses instead of any per-account `.env` setting. A non-approved
account gets a clean rejection with nothing stored.

**Ingest, triage, and digest all process every provisioned account (every
row in `accounts`) in one run — no per-account `.env` swapping:**

```bash
python -m ingest    # fetch, clean, chunk, embed, store — safe to re-run
python -m triage     # summarize + grade urgency for yesterday's emails (Eastern)
python -m digest      # send each account's digest, tracked in digest_log to avoid double-sends
```

A failure acquiring a token for one account (e.g. a revoked grant) is
logged and skipped — it doesn't stop the other accounts in the same run.

## Project layout

```
app.py                  # Flask app: /, redirects to auth if not logged in
config.py               # env var loading
auth/
  accounts.py            # accounts/approved_users table access, DB-backed token acquisition
  crypto.py               # Fernet encrypt/decrypt for stored refresh tokens
  msal_client.py           # builds the MSAL app
  routes.py                 # /auth/login, /auth/callback (allowlist-gated)
graph/
  client.py              # Graph API calls (message list, message list w/ body, send mail)
ingest/
  cleaning.py            # HTML stripping, quoted-reply/signature stripping
  chunking.py            # character-based chunking
  embeddings.py          # OpenAI embedding calls
  db.py                  # Postgres connection + upsert/replace helpers
  __main__.py             # ingestion orchestration (python -m ingest)
triage/
  time_window.py          # previous-day Eastern window (zoneinfo)
  db.py                    # retrieval queries + summary upsert
  llm.py                    # OpenAI summarization/urgency call
  __main__.py               # triage orchestration (python -m triage)
digest/
  db.py                    # digest queries + digest_log tracking
  formatting.py             # HTML digest body
  __main__.py                # digest orchestration (python -m digest)
db/init/                # Postgres schema, applied on first container boot
db/migrations/          # hand-applied schema changes for existing containers
docker-compose.yml       # local Postgres/pgvector container
```
