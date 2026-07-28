# PLANNING.md

Running log of design decisions for the RAG email agent. Add an entry
whenever a non-obvious choice is made (why X over Y), so future work
(and future Claude sessions) has the reasoning, not just the result.

See [CLAUDE.md](CLAUDE.md) for stack overview and build order.

## Open Questions

- **Idempotency / failure handling (Step 5):** What prevents a duplicate
  digest if the job runs twice in a day? What happens if summarization
  fails partway — does a partial digest send, or does the job abort and
  alert some other way (log file, fallback "digest failed" email)?
- **Time window definition:** "All emails from that day" — calendar day in
  what timezone? What happens to emails that arrive after the job runs but
  before midnight?

## Decisions

<!--
Format for each entry:

### YYYY-MM-DD — Short title
- **Decision:**
- **Why:**
- **Alternatives considered:**
- **Revisit if:**
-->

### 2026-07-27 — Email provider: Outlook via Graph API, not Gmail

- **Decision:** Use Microsoft Graph API against a personal Outlook account
  instead of Gmail.
- **Why:** Most real email volume lives in Outlook; testing against Gmail
  would mean a sparse/unrealistic corpus.
- **Alternatives considered:** Personal Gmail (original plan); forwarding
  work email into a personal Gmail account (rejected — extra indirection,
  no real benefit over just using the account where the mail already is).
- **Revisit if:** a second provider is ever needed.

### 2026-07-27 — No mail-provider abstraction layer

- **Decision:** Build directly against Graph API, no `MailProvider`
  interface.
- **Why:** Single provider for v1; abstraction adds complexity with no
  present benefit.
- **Alternatives considered:** Interface/abstraction layer for future
  provider-swapping.
- **Revisit if:** a second provider becomes a real requirement.

### 2026-07-27 — Self-send digest, not general auto-send

- **Decision:** Agent may send email, but only a daily digest to the
  user's own hardcoded address. No capability to send to arbitrary or
  dynamic recipients.
- **Why:** This is a narrower, safer exception to the original "no
  auto-send" rule — worst case is a bad summary landing in the user's own
  inbox, not an unwanted message reaching a third party.
- **Alternatives considered:** Keeping strict no-auto-send until Step 5
  (drafts only, human sends manually).
- **Revisit if:** send-on-behalf-of-user to others is ever considered —
  that remains out of scope and needs its own guardrail design.

### 2026-07-27 — Scheduling: local cron, not cloud

- **Decision:** Use local cron / OS task scheduler to run the pipeline
  daily during development.
- **Why:** Cloud infra (e.g. EventBridge + Lambda) is premature while the
  pipeline is still actively changing.
- **Alternatives considered:** AWS EventBridge + Lambda.
- **Revisit if:** the pipeline is stable and needs to run without the dev
  machine being on.

### Step 1 — OAuth + Basic Fetch

**Status: done.** Implemented per the plan below and confirmed working
end-to-end — Azure AD app registered, auth code + PKCE flow completes, and
`/` returns real recent messages from Graph.

#### Project scaffolding

```
rag-email-agent/
  app.py                  # Flask app factory / entry point
  config.py               # env var loading (CLIENT_ID, TENANT, REDIRECT_URI, SCOPES)
  auth/
    __init__.py
    msal_client.py        # builds MSAL PublicClientApplication, loads/saves token cache
    routes.py              # /login, /auth/callback (Flask blueprint)
  graph/
    __init__.py
    client.py              # thin wrapper: get_recent_messages()
  .env.example             # documents required env vars, no real values
  requirements.txt         # flask, msal, requests, python-dotenv
  token_cache.bin          # gitignored — MSAL serialized cache, treated as a credential
```

Only what step 1 needs — no chunking/embedding/pgvector scaffolding yet, per
build-order isolation.

#### Azure AD app registration (one-time, manual, in Azure Portal)

1. portal.azure.com → **Microsoft Entra ID** → **App registrations** → **New
   registration**.
2. Name: `rag-email-agent-dev` (or similar).
3. **Supported account types:** "**Personal Microsoft accounts only**" — this
   matches the personal-Outlook-only scope decision and prevents work/org
   accounts from ever being able to sign in.
4. **Authentication** blade → add platform **"Mobile and desktop
   applications"** with redirect URI `http://localhost:5000/auth/callback`,
   then set **"Allow public client flows" = Yes**. This lets us skip a client
   secret entirely (see flow choice below).
5. **API permissions** → Add a permission → Microsoft Graph → Delegated
   permissions → `Mail.Read`, `Mail.Send` → Add. No admin consent needed —
   this is a personal account, so consent happens at first interactive login.
6. Skip **Certificates & secrets** — not needed for a public client.
7. Record the **Application (client) ID**. Use authority
   `https://login.microsoftonline.com/consumers` in MSAL (the `consumers`
   tenant is for personal Microsoft accounts specifically, tighter than
   `common`).

#### MSAL flow choice: Authorization Code + PKCE (recommended over device code)

**Recommendation: Authorization Code flow with PKCE, public client (no
secret), via a Flask route.**

Why over device code flow:
- We already have Flask in the stack — adding `/login` and `/auth/callback`
  routes is a small, natural fit, not extra infrastructure.
- Microsoft's own guidance is to use device code flow only when a browser
  genuinely isn't available (headless boxes, IoT, CLI-only tools). We have a
  browser on the dev machine, so auth code flow is the "correct" flow, not
  just the more familiar one.
- Interaction is only needed **once**. After the first login, MSAL's token
  cache holds a refresh token and `acquire_token_silent()` handles all
  subsequent runs — including the unattended cron execution planned for step
  5 — without any further browser or device-code interaction. Both flows
  converge to the same unattended story after first auth, so this isn't a
  point in device code's favor.
- PKCE (no client secret) keeps a confidential secret out of the picture
  entirely, which matters more than usual since this is a personal-account,
  local-machine app with no secrets-management infra.

When device code flow would be preferable instead: a quick throwaway CLI
spike with zero Flask wiring. Not the case here since Flask routes are cheap
to add and we want the real flow working end-to-end for step 1's stated goal
("confirm auth + basic Graph API access works end to end").

#### Token storage

- Use MSAL's `SerializableTokenCache`, persisted to `token_cache.bin` in the
  project root.
- Add `token_cache.bin` to `.gitignore` explicitly (verify it's covered —
  don't assume the existing `.gitignore` catches this filename).
- Treat this file as a credential: it contains a refresh token capable of
  silently minting new access tokens for `Mail.Read`/`Mail.Send`.
- **Open question for later:** plain file cache is fine for step 1 dev use,
  but before step 5 (unattended production cron) we should revisit whether
  to move it to the OS keychain via the `keyring` package instead of a
  plaintext file on disk. Not blocking step 1.

#### Basic fetch call

- After token acquisition, call Graph directly with `requests`:
  `GET https://graph.microsoft.com/v1.0/me/messages?$top=10&$select=subject,from,receivedDateTime`
  with header `Authorization: Bearer <access_token>`.
- Print/log the returned subjects + senders to confirm end-to-end access.
  This is the full scope of step 1 — no parsing, chunking, or persistence.
- No Graph SDK dependency for now; raw `requests` calls keep the dependency
  surface small while we only need one endpoint.

#### Open question raised by this plan

- Confirm `.gitignore` actually excludes `token_cache.bin` and `.env` before
  first run (don't just assume — check it).

### 2026-07-28 — Postgres via local Docker, embeddings via local Ollama model

- **Decision:** Run Postgres/pgvector as a local Docker container for dev.
  Generate embeddings with a local Ollama model rather than a paid API.
- **Why:** Docker keeps the dev DB disposable/rebuildable and isolated from
  the host machine. Ollama was chosen specifically to avoid per-token cost —
  user explicitly does not want to spend money on this project, and accepts
  the tradeoff of lower embedding quality/speed than a hosted model like
  OpenAI's.
- **Alternatives considered:** Native Postgres install (more manual setup,
  rejected for dev convenience); OpenAI embeddings API (better quality/speed,
  rejected on cost grounds).
- **Revisit if:** local embedding quality turns out to be a real problem for
  retrieval relevance in step 3 — could swap in a paid API later since
  nothing here should hard-couple the pipeline to Ollama specifically.

### Step 2 — Chunk + Embed

**Status: done.** Ran end-to-end — 50 most recent emails fetched, cleaned
(HTML stripped, quoted replies/signatures stripped), chunked (1000 chars /
200 overlap), embedded via local Ollama (`nomic-embed-text`, 768 dims), and
upserted into Postgres/pgvector.

#### Postgres / pgvector (local Docker)

- `docker-compose.yml` with the `pgvector/pgvector:pg16` image (Postgres 16
  with the extension pre-built in — no manual extension compilation).
- Named Docker volume for data persistence across container restarts.
- New `.env` vars: `DATABASE_URL` (or discrete `POSTGRES_HOST/PORT/DB/USER/PASSWORD`).
- A small init/migration step that runs `CREATE EXTENSION IF NOT EXISTS vector;`
  and creates the schema below.

#### Schema (proposed)

```sql
emails (
  id               serial primary key,
  graph_message_id text unique not null,  -- idempotency key, ties back to Graph
  subject           text,
  sender            text,
  received_at       timestamptz,
  cleaned_body      text,                  -- after HTML/quote stripping
  fetched_at        timestamptz default now()
)

email_chunks (
  id           serial primary key,
  email_id     int references emails(id) on delete cascade,
  chunk_index  int,
  chunk_text   text,
  embedding    vector(768)                 -- dimension depends on Ollama model chosen
)
```

`graph_message_id` as the idempotency key means re-running ingestion is
safe — matches the idempotency concerns already flagged for step 5.

#### Embedding model (proposed): `nomic-embed-text` via Ollama

- Pull with `ollama pull nomic-embed-text` — 768-dim, widely used for exactly
  this kind of local/free embedding task, reasonable quality-for-size.
- Call via Ollama's local REST API (`POST http://localhost:11434/api/embeddings`)
  with plain `requests` — no new heavy client dependency needed.
- **Needs confirmation:** open to a different Ollama model if you already
  have one pulled/preferred.

#### Chunking strategy (proposed, open for discussion)

- Parse each email body: strip HTML down to text, strip quoted
  reply/forward chains and signature blocks (most of a personal inbox's
  "content" is in the top of the message, not the quoted history below it).
- Split the cleaned body into chunks by size (e.g. ~500 tokens, ~50 token
  overlap) — but many personal emails are short enough that this may mean
  most emails end up as a single chunk anyway, which is fine.
- Implement chunking directly (regex/manual splitting) rather than pulling
  in LangChain or another framework — keeps the dependency surface small,
  consistent with not adding abstraction we don't need yet.
- **Open question:** which library for HTML stripping / quote detection —
  `beautifulsoup4` for HTML is an easy, well-worn choice; quote/signature
  stripping is more custom logic. Flagging rather than deciding silently.

#### Pipeline shape (isolated to step 2 — no retrieval or dispatch yet)

- New `ingest.py` (or `ingest/` module): fetch emails via an extended
  `graph/client.py` (needs full message body now, not just
  subject/from/receivedDateTime), clean + chunk + embed, upsert into
  Postgres keyed on `graph_message_id`.
- Explicitly not building retrieval/query code yet — that's step 3.

#### Open questions this plan raises

- Confirm `nomic-embed-text` vs. another Ollama model.
- Confirm chunk size/overlap numbers, or leave as a tunable default to
  adjust once we see real chunk counts.
- HTML/quote-stripping approach — proposed above, not yet confirmed.

### Step 3 — Retrieval + Summarization/Triage

_(not started)_

### Step 4 — Daily Digest Dispatch

_(not started — see Open Questions above, must be resolved before implementation)_

### Step 5 — Automation + Guardrails

_(not started)_
