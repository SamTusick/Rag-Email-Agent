# Rag-Email-Agent

A RAG-based email agent that connects to a personal Outlook account, indexes
email content for retrieval, and (eventually) sends a daily digest email
summarizing and triaging that day's messages by urgency.

See [CLAUDE.md](CLAUDE.md) for the full build order and current status, and
[PLANNING.md](PLANNING.md) for the design-decision log.

## Stack

- Python, Flask
- Postgres + `pgvector` for embedding storage/similarity search (local Docker container)
- Microsoft Graph API via MSAL (personal Outlook account, `Mail.Read` + `Mail.Send` scopes)
- Ollama (local, free) for generating embeddings — currently `nomic-embed-text`

## Setup

### 1. Azure AD app registration

Register an app at [portal.azure.com](https://portal.azure.com) → Microsoft
Entra ID → App registrations. Details (account type, redirect URI, API
permissions) are documented in [PLANNING.md](PLANNING.md) under Step 1 —
follow those exactly, since the auth flow depends on the registration
matching `config.py`/`.env`.

### 2. Ollama

Install [Ollama](https://ollama.com), then pull the embedding model:

```bash
ollama pull nomic-embed-text
```

### 3. Postgres (Docker)

```bash
docker compose up -d
```

This starts a `pgvector/pgvector` Postgres container and runs
`db/init/001_schema.sql` on first boot to create the schema.

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
`FLASK_SECRET_KEY`, and Postgres credentials (these initialize the Docker
container, so pick values before first running `docker compose up`, or run
`docker compose down -v` to reinit if you change them after). See
`.env.example` for the full list with comments.

## Running

**Authenticate and do a basic fetch:**

```bash
python app.py
```

Visit `http://localhost:5000` — you'll be redirected to Microsoft login on
first run, then see your most recent messages as JSON.

**Ingest emails (clean, chunk, embed, store):**

```bash
python -m ingest
```

Requires an existing `token_cache.bin` (i.e. you've logged in via the app
at least once already) and the Postgres container + Ollama running. Fetches
the most recent 50 messages, cleans and chunks each one, embeds the chunks
locally via Ollama, and upserts everything into Postgres — safe to re-run
(idempotent on Graph message ID).

## Project layout

```
app.py                  # Flask app: /, redirects to auth if not logged in
config.py               # env var loading
auth/
  msal_client.py        # MSAL public client, token cache
  routes.py              # /auth/login, /auth/callback
graph/
  client.py              # Graph API calls (message list, message list w/ body)
ingest/
  cleaning.py            # HTML stripping, quoted-reply/signature stripping
  chunking.py            # character-based chunking
  embeddings.py          # Ollama embedding calls
  db.py                  # Postgres upsert/replace helpers
  __main__.py             # ingestion orchestration (python -m ingest)
db/init/                # Postgres schema, applied on first container boot
docker-compose.yml       # local Postgres/pgvector container
```
