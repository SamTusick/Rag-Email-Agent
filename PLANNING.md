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

### 2026-08-03 — Scheduling: move to cloud (Lambda + EventBridge), superseding local-cron decision

- **Decision:** Run ingest + digest jobs on AWS Lambda, triggered by
  EventBridge on a schedule, replacing local cron.
- **Why:** Triggers the revisit condition from the 2026-07-27 local-cron
  decision — the pipeline is stable and needs to run without the dev
  machine being on. Also motivated by: wanting a real automatic-scheduling
  system (not dependent on my laptop being awake), hands-on cloud infra
  experience, and easier onboarding for other users (see multi-user
  decision below) who shouldn't need to run anything locally.
- **Alternatives considered:** Windows Task Scheduler (more durable than
  cron but still requires the dev machine on — doesn't solve the core
  problem); staying local indefinitely.
- **Revisit if:** cost becomes a real problem at current scale (unlikely
  given low invocation volume — see cost note below), or usage grows
  enough that RDS/Lambda need to scale up.

### 2026-08-03 — Embeddings: switch from Ollama to hosted API (OpenAI), superseding local-Ollama decision

- **Decision:** Use a hosted embeddings API (OpenAI) instead of local
  Ollama, in both dev and prod (one code path, not two).
- **Why:** Not the quality-driven revisit condition anticipated in the
  2026-07-28 decision — the actual trigger is that Lambda can't run a
  local Ollama model at all, so the cloud move forces this regardless of
  quality. Standardizing on hosted embeddings everywhere (rather than
  Ollama in dev / hosted in prod) avoids maintaining two embedding code
  paths and two sets of chunk vectors to reconcile.
- **Alternatives considered:** Keep Ollama for local dev, hosted API only
  in Lambda (rejected — two code paths, dimension mismatch between models
  would complicate the schema/retrieval layer for no real benefit).
- **Revisit if:** hosted API cost becomes meaningful at higher usage —
  currently low-volume enough (few users, daily digest) that this is not
  a concern.

### 2026-08-03 — Multi-user support: allowlist-gated, no separate identity system

- **Decision:** Support multiple users (me + a small group of friends/
  coworkers), each connecting their own Outlook account, gated by an
  allowlist checked at OAuth callback — before any account provisioning
  or resource use. No Cognito or other app-native login; Microsoft OAuth
  itself is the identity proof.
- **Why:** Project goal expanded from single-account personal tool to
  something a small group can actually use. Full open multi-tenancy
  (anyone can sign up) was rejected specifically to control cost exposure
  — an allowlist stops unapproved users from ever reaching Lambda/RDS/
  embedding API calls, not just from seeing others' data. Cognito was
  considered and rejected as unnecessary complexity: OAuth already proves
  who someone is, so a second app-native identity system would be
  authentication with no purpose it doesn't already serve.
- **Alternatives considered:** Cognito user pool with app-native login
  layered on top of OAuth (rejected — redundant identity system, real SaaS
  pattern but not needed at this scale); fully open sign-up with no
  allowlist (rejected — cost exposure to anyone who finds the app URL).
- **Data model implication:** every table (`emails`, `email_chunks`, and
  any future digest/settings tables) gets an `account_id` (or `user_id`)
  column scoping rows to a tenant. `approved_users` table (or config list)
  holds permitted emails, managed manually by me as admin.
- **My two Outlook accounts** (personal + professional) are the first two
  tenants — used to build and validate multi-account scoping before
  onboarding anyone else.
- **Revisit if:** the group grows large enough that manual allowlist
  management (me adding emails by hand) becomes a real bottleneck — a
  self-serve request/approval flow would be the next step, not Cognito.
- **Related open work:** Step 2's schema (`emails`, `email_chunks`) predates
  this decision and does not yet have `account_id` — needs a migration
  before Step 3 (retrieval) can be account-aware. RDS Postgres replaces the
  local Docker Postgres container for the same reason as the compute move.

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

**Status: done, including the account_id migration below.** Migration
applied against the running container — 50 `emails` rows and 182
`email_chunks` rows backfilled with `account_id = 'stusick@outlook.com'`,
composite unique constraint and both indexes confirmed in place, zero NULL
`account_id` rows remaining.
Ran end-to-end — 50 most recent emails fetched, cleaned (HTML stripped,
quoted replies/signatures stripped), chunked (1000 chars / 200 overlap),
embedded via local Ollama (`nomic-embed-text`, 768 dims), and upserted into
Postgres/pgvector.

#### Proposed: add `account_id` to `emails` / `email_chunks` (schema + ingestion only)

Scope for this session, per the 2026-08-03 multi-user decision's "related
open work" — **only** the column + ingestion write path. Not touching
scheduling, Lambda, OAuth, or the allowlist.

**How `account_id` gets populated, without touching OAuth:** there's no
in-scope way to ask Graph "whose mailbox is this" — the natural call
(`GET /me`, for `mail`/`userPrincipalName`) needs the `User.Read` delegated
permission, which isn't part of the current `Mail.Read` + `Mail.Send` grant.
Widening scope needs its own flag-and-discuss per the working conventions,
and that's an OAuth-surface change this session explicitly excludes. So
instead: a new required `ACCOUNT_ID` value in `.env`, read once per ingest
run and stamped onto every row that run writes. Recommend setting it to the
account's own email address (forward-compatible with whatever a real
OAuth-derived identity value looks like later) — e.g. run ingest once with
`ACCOUNT_ID` set to the personal account's address, swap `.env`/
`token_cache.bin` and run again for the professional account, to validate
scoping across both without building real multi-account auth yet.

**Schema changes — applied via migration, not a volume wipe** (user
preference: preserve the existing 50-email test dataset instead of
`docker compose down -v`). Two files now stay in sync by hand, since there's
no migration-tracking framework yet:

- `db/init/001_schema.sql` updated to the **target** shape directly —
  `account_id TEXT NOT NULL` on both tables, `emails` unique constraint is
  `UNIQUE (account_id, graph_message_id)`, plus a plain btree index on
  `account_id` on each table. This only affects *fresh* volumes going
  forward (first boot on an empty one) — doesn't touch the already-running
  container.
- `db/migrations/migrations.sql` (new) — applied by hand against the
  already-running container to bring it up to the same target shape without
  losing data: add both `account_id` columns nullable, backfill existing
  rows with the current account's value, set `NOT NULL`, swap the
  `graph_message_id`-only unique constraint for the composite one, add the
  two account_id indexes.
- Known limitation, same as already documented for `db/init/`: manually
  keeping two files in sync is fine at this project's size, but is exactly
  the kind of thing a real migration tool (Alembic, etc.) exists to solve —
  not introducing one yet since this is still a single-developer, low
  schema-churn project.
- Idempotency key on `emails` becomes the **composite** `(account_id,
  graph_message_id)`, not `graph_message_id` alone — needed once more than
  one mailbox can land the same-shaped IDs in the same table.
- `email_chunks.account_id` is denormalized (redundant with
  `emails.account_id` via `email_id`) rather than requiring a join — matters
  once step 3's similarity search needs to filter by account on the hot
  path. Already what the multi-user decision's data-model note called for.
- `email_chunks (email_id, chunk_index)` unique constraint is unchanged —
  still sufficient since `email_id` already scopes to one account.
- **Needs confirmation before running:** the migration's backfill step
  (`UPDATE emails SET account_id = 'your-existing-account-value'`) has a
  placeholder — needs the real value substituted (recommended: the
  account's own email address, matching the `ACCOUNT_ID` value about to go
  in `.env`) before it's run against the container.

**Code changes:**
- `config.py`: add `ACCOUNT_ID = os.environ["ACCOUNT_ID"]` (required — no
  sensible default when every row must be scoped). Add to `.env.example`
  with a comment explaining it's a manual stand-in until real OAuth-derived
  identity exists.
- `ingest/db.py`: `upsert_email(...)` gains an `account_id` parameter,
  included in the insert and the conflict target
  (`ON CONFLICT (account_id, graph_message_id)`). `replace_chunks(...)`
  gains an `account_id` parameter, included in each inserted chunk row.
- `ingest/__main__.py`: read `config.ACCOUNT_ID` once, pass it through to
  both calls above.

**Not doing:** no `approved_users`/allowlist table, no Graph scope changes,
no code path for running ingest against multiple accounts in one process —
that's the deferred OAuth/allowlist work.

#### Done: switch embeddings from Ollama to OpenAI `text-embedding-3-small`

Implemented and verified — `email_chunks.embedding` migrated to
`vector(1536)`, `db/init/001_schema.sql` updated to match for fresh
installs, all Ollama config/code removed, and `python -m ingest` re-run
successfully: 176 chunks across all ingested emails, uniformly 1536-dim,
confirmed via `vector_dims(embedding)`.

Executes the 2026-08-03 "Embeddings: switch from Ollama to hosted API"
decision above. Scope for this session — the embeddings swap only, validated
locally. **Not** touching Lambda, EventBridge, or RDS yet (those are
separate, deferred moves per that same decision and the scheduling one next
to it).

**`ingest/embeddings.py`** — replace the Ollama call with a plain `requests`
call to OpenAI's REST endpoint, matching the existing style (Graph and
Ollama clients are both raw `requests`, no SDK) rather than adding the
`openai` package as a new dependency for one endpoint:

```python
import requests

import config


def embed_text(text):
    response = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json={"model": config.OPENAI_EMBEDDING_MODEL, "input": text},
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]
```

Same `embed_text(text) -> list[float]` signature as before, so
`ingest/__main__.py`'s call site (`[embed_text(c) for c in chunks]`) doesn't
need to change shape — one call per chunk, same as the Ollama version.
(Not batching multiple chunks into one call, even though OpenAI's endpoint
supports a list `input` — that'd be a real optimization but wasn't asked
for and adds a shape change; flagging as a possible future improvement, not
doing it now.)

**`config.py`** — add the new required key, drop the Ollama-only values
since nothing else will use them (one code path, not a toggle — matches
the "why" already recorded in the 2026-08-03 decision):

```python
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
```

Remove `OLLAMA_BASE_URL` and `OLLAMA_EMBEDDING_MODEL` (and their
`.env.example` entries) rather than leaving them as dead config — nothing
will reference them once this lands.

**`ingest/__main__.py`** — `EXPECTED_EMBEDDING_DIM = 768` becomes `1536`
(text-embedding-3-small's output size), and the dimension-mismatch error
message's reference to `config.OLLAMA_EMBEDDING_MODEL` becomes
`config.OPENAI_EMBEDDING_MODEL`.

**`requirements.txt`** — no change (no new dependency; `requests` already
covers this).

**Handling the existing 768-dim rows — re-embed, not reconcile:** you asked
for a re-embed rather than mixing dimensions, and that's also the only
option that actually works — pgvector has no defined cast between two
fixed-size `vector(N)` types, so `ALTER COLUMN ... TYPE vector(1536)` will
fail outright while any 768-dim values remain in the column. So: clear
`email_chunks` entirely (not just the embedding column — `content` and
`chunk_index` are trivially regenerated by re-running ingest against the
same 50 emails, so there's no value in trying to preserve them separately),
change the column type on the now-empty table, then re-run `python -m
ingest` to regenerate everything through the new pipeline. `emails` rows are
untouched — they're provider data, not embedding-dependent, and
`upsert_email`'s `ON CONFLICT` makes re-running ingest a no-op update for
them.

New file `db/migrations/002_switch_to_openai_embeddings.sql` (applied by
hand against the running container, same pattern as
`001_add_account_id.sql`):

```sql
DELETE FROM email_chunks;
ALTER TABLE email_chunks ALTER COLUMN embedding TYPE vector(1536);
```

`db/init/001_schema.sql` also updated to `VECTOR(1536)` (with its comment
updated to reference `text-embedding-3-small`) so a fresh volume matches —
same two-files-in-sync-by-hand approach already accepted for `account_id`.

**`.env.example`** — add `OPENAI_API_KEY=` (blank, credential) and
`OPENAI_EMBEDDING_MODEL=text-embedding-3-small` (documents the default);
remove the two `OLLAMA_*` entries.

**Cost note:** text-embedding-3-small is priced per token (~$0.02 per 1M
tokens at time of writing) — re-embedding 182 chunks from 50 short emails
is a trivial fraction of a cent. Not a real cost concern at this volume,
flagging only because cost was an explicit constraint in earlier decisions.

**Verification after implementing:**
1. Apply the migration against the running container (same
   `docker compose exec -T db psql ...` pattern used for the account_id
   migration).
2. Confirm `\d email_chunks` shows `embedding` as `vector(1536)` and the
   table is empty.
3. Set `OPENAI_API_KEY` in `.env`, run `python -m ingest`.
4. `SELECT vector_dims(embedding) FROM email_chunks LIMIT 1;` → 1536.
5. `SELECT count(*) FROM email_chunks;` → back to the expected chunk count
   (182, assuming the same 50 messages and unchanged chunking logic).

**Not doing:** no Lambda/EventBridge/RDS changes, no OpenAI SDK dependency,
no batched embedding calls, no dual embedding-provider code path.

**Failure handling (confirmed):** no new retry/backoff logic. An OpenAI API
failure still crashes `python -m ingest` uncaught, same as any other
exception today. Accepted because it's already safe to do so — each email's
writes are transactional, and re-running is idempotent — and because real
failure handling is explicitly step 5's job, not this addendum's. Revisit
if 429 rate-limiting on 182 back-to-back calls turns out to be a real
problem in practice.

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
