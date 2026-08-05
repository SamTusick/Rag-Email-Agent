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

## Decisions

<!--
Format for each entry:

### YYYY-MM-DD — Short title
- **Decision:**
- **Why:**
- **Alternatives considered:**
- **Revisit if:**
-->

### 2026-08-05 — Per-account priority context for triage

- **Decision:** New nullable `accounts.priority_context` column (free
  text), injected into `triage/llm.py`'s system prompt when set. Managed
  manually via `psql`, same pattern as `approved_users`.
- **Why:** Different accounts have genuinely different notions of
  "urgent" (e.g. `samtusick@outlook.com` is career-focused — jobs,
  recruiters, LinkedIn matter — while `stusick@outlook.com` is personal).
  Free text over a structured keyword/category list because triage
  already uses an LLM to make a contextual judgment call, not keyword
  matching — "this matters because it's from a recruiter" is exactly the
  kind of semantic reasoning an LLM already does well, and a keyword list
  would require hand-enumerating recruiter domains/keywords up front while
  still missing novel ones.
- **Alternatives considered:** structured keyword/sender-domain list per
  account (rejected — rigid, needs separate matching logic, doesn't fit
  what triage already does); a settings UI for self-service editing
  (rejected for now — no admin/user-facing surface exists yet, manual SQL
  matches the existing allowlist-management convention).
- **Revisit if:** manual SQL editing becomes a real friction point once
  there are enough accounts that hand-tuning each one's text doesn't scale.

#### Proposed plan: `priority_context`

**Status: done.** Implemented and verified against real data — the exact
same real email ("Recruiter from Freddie Mac just sent you a message on
WayUp", already summarized `low` before this feature existed) was fed
through `summarize_and_grade` twice with identical inputs except
`priority_context`: `None` → `low` (matches its original grading,
confirming default behavior is unchanged), the `samtusick@outlook.com`
career-focused text → `high`. Clean, isolated proof the customization
actually shifts judgment, not just that it runs without erroring.
`stusick@outlook.com` left `NULL` (unchanged generic behavior, matches
"personal, no special customization" intent).

- **Schema:** `ALTER TABLE accounts ADD COLUMN priority_context TEXT;` —
  new `db/migrations/006_add_priority_context.sql`, plus the matching
  column added to `accounts` in `db/init/001_schema.sql` for fresh installs.
- **`auth/accounts.py`:** new `get_priority_context(conn, account_id)` —
  `SELECT priority_context FROM accounts WHERE account_id = %s`, returns
  `None` if unset. Kept separate from `get_all_account_ids` rather than
  folding it in — `ingest`/`digest` don't need this data at all, no reason
  to change their call shape.
- **`triage/__main__.py`:** `triage_account` fetches
  `priority_context = get_priority_context(conn, account_id)` **once per
  account**, not per email — it doesn't vary within a batch, so fetching
  it per-email would just be a wasted repeated query. Passed through to
  `summarize_and_grade`.
- **`triage/llm.py`:** the static `SYSTEM_PROMPT` constant becomes a base
  template plus a small `_build_system_prompt(priority_context)` helper
  that appends an "Additional context for this account's priorities: ..."
  line when set, unchanged when not. `summarize_and_grade` gains a
  `priority_context` parameter.
- **Management:** e.g.
  ```sql
  UPDATE accounts SET priority_context =
    'Career-focused account: emails about jobs, recruiters, LinkedIn '
    'messages, and career opportunities are important/high priority. '
    'Routine promotional emails stay low priority regardless of sender.'
  WHERE account_id = 'samtusick@outlook.com';
  ```
  `stusick@outlook.com` stays `NULL` — unchanged generic behavior, matching
  "personal, no special customization."

##### Verification plan

1. Compile-check.
2. Apply the migration, confirm the column exists.
3. Set `priority_context` for `samtusick@outlook.com` only, per the example
   above.
4. Re-run `python -m triage` for both accounts (idempotent — upserts by
   `email_id`, safe to regenerate). Compare urgency grading on similar
   career/promotional emails between the two accounts to confirm the
   customization actually shifts judgment sensibly, not just that it runs
   without erroring.

### 2026-08-05 — Bug found during verification: no `requests` timeouts anywhere, causing a real hang

- **Symptom:** `python -m triage` across two accounts hung indefinitely —
  no output, static memory footprint, process alive but making no
  progress for 30+ minutes.
- **Root cause:** none of the five `requests.get`/`requests.post` calls in
  the codebase (`triage/llm.py`, `graph/client.py` ×3, `ingest/embeddings.py`)
  set a `timeout`. A connection that never responds (no error, no data)
  blocks `requests` forever — and since the existing retry logic in
  `triage/llm.py`/`graph/client.py::send_mail` only runs *after* a response
  comes back, it couldn't help a call that never returns at all. This is a
  different failure mode than the OpenAI-verification flakiness handled
  earlier (that one always returned an HTTP response, just sometimes a bad
  one).
- **Fix:** added `timeout=60` to the two OpenAI calls (LLM completions can
  legitimately take a while) and `timeout=30` to the three Graph API calls.
  Fixed all five call sites, not just the one that happened to hang first —
  leaving the same latent gap in the other four would just mean the next
  hang picks a different call site.
- **Revisit if:** a legitimate slow request starts hitting these timeouts
  in practice — bump the specific value, not the philosophy (a bounded
  failure is always better than an unbounded hang).

### 2026-08-04 — Wire DB-backed tokens into ingest/triage/digest, retire token_cache.bin path

- **Decision:** Replace the manual `ACCOUNT_ID`/`token_cache.bin` path in
  `ingest`, `triage`, and `digest` with the DB-backed `get_token_for_account`
  built during the OAuth + allowlist step. Each command now iterates over
  accounts present in the `accounts` table (i.e. real OAuth-provisioned,
  allowlist-approved accounts) rather than acting on a single account read
  from `.env`.
- **Why:** The OAuth + allowlist step built and verified DB-backed token
  storage, but nothing downstream used it yet — accounts could be approved
  and stored with zero effect on the actual pipeline. This closes that gap
  and is also the natural point to support multiple accounts per run
  instead of one manual `.env` swap per run.
- **Alternatives considered:** keep both paths indefinitely (rejected —
  confusing to maintain two token sources, and defeats the purpose of
  having built DB-backed storage).
- **Revisit if:** N/A — this is a straightforward retirement of the old
  path once the new one is proven correct.

#### Proposed plan: wire ingest/triage/digest to the accounts table

**Status: done.** Implemented and verified end-to-end against two real
Microsoft accounts, including genuine multi-account processing in a single
run and real failure isolation. Full repo-grep-verified — nothing missed
beyond what's listed below.

##### `auth/accounts.py` — new `get_all_account_ids(conn)`

`SELECT account_id FROM accounts`, returns a list. Nothing like this
exists yet.

##### `ingest/__main__.py` and `triage/__main__.py` — extract per-account
helper, loop over `get_all_account_ids`

Fixes a real latent bug: triage's current `if not emails: ...; return`
exits `main()` entirely on the first account with nothing to triage —
extracted into a helper function, that return only exits the helper, so
the outer loop correctly continues to the next account.

**Failure isolation: one `try/except Exception` per account, wrapping the
*entire* per-account body**, not just the token fetch narrowly. With only
the token fetch wrapped, an uncaught crash mid-account (e.g. an embedding
API error) would abort the whole process and silently skip every remaining
account until the next scheduled run — the wider catch is what actually
satisfies "don't let one account's problem kill it for everyone" once more
than one account shares a run. Confirmed safe to keep reusing the same
`conn` across iterations after a caught exception: `get_connection()`
already sets `autocommit = True` and all writes are in their own explicit
`with conn.transaction():` blocks, so nothing needs an extra
`conn.rollback()`.

**Triage does not call `get_token_for_account` at all** — it never calls
Graph (pure Postgres + OpenAI). An earlier draft had it fetch-and-discard a
token purely as an "is this account's grant still valid" liveness check,
but that adds a real network dependency on Microsoft's identity platform
for a step that structurally doesn't need it. The same goal is already
achieved for free: once `ingest` correctly gates on a valid token, a
revoked account simply stops getting new email rows, so triage naturally
finds nothing new without an extra network call.

##### `digest/__main__.py` — switch account source, reorder checks

Switch from `digest.db.get_account_ids_with_summaries` (a `DISTINCT`
query against `email_summaries`/`emails` filtered by the time window) to
`get_all_account_ids`, for consistency with the other two. This changes
behavior — today an account with zero summaries just never appears in the
loop; with `get_all_account_ids` it would, so add an explicit
`if not grouped: ...; continue`. Reorder so the two free checks
(already-sent, has-summaries) happen *before* the token fetch — no point
authenticating for an account with nothing to send. `get_account_ids_with_summaries`
becomes dead code (confirmed via grep: no other callers) — delete it.

##### Full retirement, not just disuse, of `token_cache.bin`/`get_token_silent`

Auditing every remaining caller confirms nothing needs file-based MSAL
caching after the above lands: `/auth/login` and `/auth/callback` each
build a fresh `PublicClientApplication` per request, and
`get_token_for_account` already passes its own decrypted refresh token
explicitly. Leaving the file-cache code around unused would be dead code,
contradicting this project's own convention (e.g. `OLLAMA_*` config was
fully removed, not left unused, when embeddings switched providers). So:

- `auth/msal_client.py` shrinks to just `build_msal_app()` returning a bare
  `PublicClientApplication` (no cache tuple) — delete `_load_cache`,
  `_save_cache`, `get_token_silent`.
- `auth/routes.py`: both call sites of `build_msal_app()` update to match
  (`app = build_msal_app()`, not `app, cache = ...`). `callback()` drops
  its now-redundant `_save_cache(cache)` call — the DB already durably
  stores the refresh token, and Lambda can't rely on the file anyway — and
  gains `session["account_id"] = email` right before the final redirect
  (this is what lets `app.py` know who just logged in, below).
- `config.py`, `.env.example`, `.env`: remove `ACCOUNT_ID` and
  `TOKEN_CACHE_PATH`. `.gitignore`: remove the `token_cache.bin` line.
- The physical leftover `token_cache.bin` file becomes a genuinely unused
  orphan — flagging as optional cleanup, not deleting it silently.

##### `app.py` — necessary side-fix, not in the originally-named file list

Removing `config.ACCOUNT_ID` breaks `app.py`'s `/` route otherwise (it
currently calls `get_token_silent(config.ACCOUNT_ID)`, and has zero DB
access today). Fixed by reading the session value `auth/routes.py` now
sets: `if not (account_id := session.get("account_id")): return
redirect(...)`, then `get_token_for_account(conn, account_id)` via a
short-lived connection, same pattern as the batch scripts.

##### `README.md`

Update setup/running sections describing `ACCOUNT_ID`/`token_cache.bin` —
same "don't leave stale docs" reasoning as above. Describe the new
behavior: each command processes every account in `accounts` in one run.

##### Not doing

No Lambda/EventBridge/cloud deployment. No new schema/migration (`accounts`/
`approved_users` already exist). No general resilience work beyond the
per-account isolation described above.

##### Verification — done

1. Compile-check: clean.
2. Single-account baseline confirmed for all three commands (`stusick@outlook.com`).
3. **Genuine multi-account proof**: approved and logged in
   `samtusick@outlook.com` for real (turned out its historical
   `emails`/`email_summaries` rows from an earlier session weren't a typo
   after all — they're this account's real data, now usable). All three
   commands processed both accounts in a single run (`ingest`: 100 messages
   across both; `triage`: 65 summaries across both; `digest`: real sends to
   both for 2026-08-04).
4. **Failure isolation proof**: corrupted `samtusick@outlook.com`'s stored
   token via `psql`, ran `ingest` — `stusick@outlook.com` fully succeeded
   (50 messages) while the other logged a clean failure and didn't block
   anything. Restored via fresh login, re-verified both healthy.
5. `app.py`'s `/` route confirmed working for both accounts via real
   browser logins (session-based, no crash — confirms the `build_msal_app()`
   signature change didn't break anything).
6. Final repo-wide grep for `ACCOUNT_ID`, `token_cache.bin`,
   `TOKEN_CACHE_PATH`, `get_token_silent` — zero code hits.

**Bug found and fixed along the way (see the 2026-08-05 entry above this
one):** none of the five `requests` calls in the codebase had a `timeout`,
which caused a real 30+ minute hang during the multi-account `triage`
verification run. Fixed all five, not just the one that hung. Also
improved the per-account failure logging (`type(exc).__name__: {exc}`) —
the original just printed `{exc}` alone, which was blank for
`cryptography.fernet.InvalidToken` (no message by default) and would have
been undiagnosable in the exact scenario this isolation logic exists for.

### 2026-08-04 — Multi-user OAuth + allowlist: encrypted token storage, manual allowlist management

- **Decision:** New `accounts` table in Postgres stores each user's OAuth
  refresh token, encrypted at rest (Fernet/`cryptography` package).
  `approved_users` table gates the OAuth callback — checked before any
  token is stored or account provisioned. Allowlist managed manually via
  direct `psql` INSERT (no admin UI/script). Built Lambda-portable from
  the start: no local file-based token storage, redirect URI read from
  config not hardcoded.
- **Why:** Local token_cache.bin doesn't survive Lambda's ephemeral
  filesystem, so DB storage is required regardless, not just a nice-to-
  have. Encryption is non-negotiable for other people's credentials.
  Manual allowlist management is fine at this scale (a handful of users,
  managed by me).
- **Known gap:** encryption key itself lives in .env for now — should
  move to AWS Secrets Manager when actually deployed to Lambda, not
  before. Flagged so this isn't mistaken for the final state.
- **Alternatives considered:** local per-account token files (rejected —
  incompatible with Lambda's ephemeral storage); admin script for
  allowlist management (rejected for now — unnecessary polish at this
  scale, manual SQL is fine).
- **Revisit if:** the encryption-key-in-.env gap hasn't been addressed by
  the time Lambda deployment actually happens — don't let this slip.

#### Proposed plan: OAuth + allowlist + encrypted token storage

**Status: done.** Implemented and verified end-to-end against real Microsoft
accounts — approved login stores an encrypted refresh token in `accounts`;
`get_token_for_account` redeems it for a working Graph access token
(confirmed by listing real messages) with zero dependency on
`token_cache.bin`; and a second, non-approved real account got a clean 403
with confirmed zero rows written anywhere. Scope: OAuth callback gating,
`accounts`/`approved_users` tables, and a new per-account token-acquisition
path. Tested locally against real accounts. **Not** touching Lambda,
EventBridge, or actual cloud deployment — that stays the next step after
this one. **Not** wiring `ingest`/`triage`/`digest` over to the new
per-account path yet — they keep using the existing local
`token_cache.bin` for now; this session only builds the new capability
they'll _eventually_ call instead.

**Verified against the installed `msal` (1.37.0) source before committing
to this design**, rather than assuming: MSAL has a genuinely **public,
documented** method for exactly this scenario —
`acquire_token_by_refresh_token(refresh_token, scopes)` — its own docstring
describes it as being for when "you have old RTs from elsewhere... want to
migrate them into MSAL." Also confirmed `acquire_token_by_auth_code_flow`'s
result dict does carry a raw `refresh_token` key through to the caller
(traced MSAL's internal `_clean_up()` — it only strips `refresh_in` and
underscore-prefixed internal keys, everything else including
`refresh_token` passes through). So this plan needs no private/underscore
APIs and no guessing about response shape.

##### Schema

```sql
CREATE TABLE approved_users (
    email      TEXT PRIMARY KEY,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    account_id              TEXT PRIMARY KEY,
    encrypted_refresh_token TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- `account_id`/`email` as the primary key directly (no surrogate `id`) —
  one row per account, natural key, matches how `account_id` is already
  used as a plain string throughout the rest of the schema.
- **Flagging, not deciding silently:** no `updated_at` on `accounts`, per
  your column list — but the refresh token _will_ get rewritten over time
  (rotation, see below), so there's no column tracking when that last
  happened. Cheap to add if you want it; leaving out since you specified
  the columns explicitly.
- `approved_users` rows added by hand via `psql` — no code needed, per your
  scope.
- New `db/migrations/005_add_accounts_and_approved_users.sql` (applied by
  hand against the running container, same pattern as the prior four
  migrations); `db/init/001_schema.sql` gets the same tables added for
  fresh installs.

##### Encryption: `auth/crypto.py`

```python
from cryptography.fernet import Fernet

import config

def encrypt(plaintext):
    return Fernet(config.TOKEN_ENCRYPTION_KEY).encrypt(plaintext.encode()).decode()

def decrypt(ciphertext):
    return Fernet(config.TOKEN_ENCRYPTION_KEY).decrypt(ciphertext.encode()).decode()
```

New config: `TOKEN_ENCRYPTION_KEY = os.environ["TOKEN_ENCRYPTION_KEY"]`
(required, no default — a credential like `FLASK_SECRET_KEY`). Generate
with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
New dependency: `cryptography`.

**🚩 Flagged exactly as asked — don't let this slip:** the key itself lives
in `.env` for now. This is temporary and must move to AWS Secrets Manager
before/at actual Lambda deployment, not after. Recorded here and in the
decision above so it isn't forgotten; the "next step after this" (actual
cloud deployment) should treat this as a blocking prerequisite, not an
afterthought.

##### Account storage + token acquisition: `auth/accounts.py`

```python
import config
from auth.crypto import decrypt, encrypt
from auth.msal_client import build_msal_app

def is_approved(conn, email):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM approved_users WHERE email = %s", (email,))
        return cur.fetchone() is not None

def upsert_account(conn, account_id, refresh_token):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO accounts (account_id, encrypted_refresh_token)
            VALUES (%s, %s)
            ON CONFLICT (account_id) DO UPDATE
              SET encrypted_refresh_token = EXCLUDED.encrypted_refresh_token
            """,
            (account_id, encrypt(refresh_token)),
        )

def get_token_for_account(conn, account_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT encrypted_refresh_token FROM accounts WHERE account_id = %s",
            (account_id,),
        )
        row = cur.fetchone()
    if not row:
        return None

    app, _cache = build_msal_app()
    result = app.acquire_token_by_refresh_token(decrypt(row[0]), config.GRAPH_SCOPES)
    if not result or "access_token" not in result:
        return None

    if "refresh_token" in result:  # rotation — re-store the new one
        upsert_account(conn, account_id, result["refresh_token"])

    return result["access_token"]
```

- Refresh tokens rotate on Microsoft's identity platform — every
  successful redemption re-encrypts and re-stores whatever new
  `refresh_token` comes back, so the stored value never goes stale from
  our own inaction.
- This is the function `ingest`/`triage`/`digest` will eventually call
  instead of `auth.msal_client.get_token_silent()` — not wired in this
  session, per scope.

##### OAuth callback: `auth/routes.py`

```python
@bp.route("/callback")
def callback():
    app, cache = build_msal_app()
    flow = session.pop("auth_flow", {})
    result = app.acquire_token_by_auth_code_flow(flow, request.args)

    if "access_token" not in result:
        return f"Auth failed: {result.get('error_description', result)}", 400

    email = result["id_token_claims"]["preferred_username"]

    conn = get_connection()
    try:
        if not is_approved(conn, email):
            return "This account is not approved to use this application.", 403

        _save_cache(cache)  # existing local-cache path, unchanged
        if "refresh_token" in result:
            upsert_account(conn, email, result["refresh_token"])
    finally:
        conn.close()

    return redirect(url_for("fetch_messages"))
```

- **Approval check happens before any write** — `_save_cache` and
  `upsert_account` both sit after the `is_approved` gate, so a rejected
  login leaves zero trace in either the local cache or the `accounts`
  table. That's the literal mechanism satisfying "no partial provisioning."
- `email` comes from the ID token's `preferred_username` claim, already
  present in the auth-code-flow result (MSAL requests `openid profile` by
  default) — no new Graph scope, same reasoning already used back when
  this was first considered for `account_id` in step 2.
- Reuses `ingest.db.get_connection` (already `autocommit=True`), closed
  explicitly at the end of the request — a Flask request handler is a
  different lifetime than the batch scripts' one-connection-per-run
  pattern, so this doesn't reuse a long-lived connection across requests.

##### Redirect URI

Already satisfied — `auth/routes.py`'s `login()` already reads
`redirect_uri=config.REDIRECT_URI` from config, not hardcoded. No change
needed; noting it explicitly since you flagged it as a requirement.

##### Not doing

No Lambda/EventBridge/cloud deployment. No admin UI/script for
`approved_users` (manual `psql`, per your scope). No wiring of
`get_token_for_account` into `ingest`/`triage`/`digest` yet.

##### Verification plan

1. Apply the migration; confirm both tables exist via `psql`.
2. Manually `INSERT` your test account's email into `approved_users`.
3. `python app.py` → `/auth/login` → complete real Microsoft login →
   confirm redirect succeeds, then `SELECT * FROM accounts;` shows a row
   with an opaque (non-plaintext) `encrypted_refresh_token`.
4. Rejection path: attempt login with an email _not_ in `approved_users` →
   confirm a clean 403 and confirm **no** row was written to `accounts`.
5. Exercise `get_token_for_account` directly (e.g. a throwaway REPL call)
   and use the returned access token against `graph.client.get_recent_messages`
   to prove the encrypted-refresh-token path independently produces a
   working Graph access token, without touching `token_cache.bin` at all.

### 2026-08-04 — Digest dispatch: separate command, templated list, tracked send-state

- **Decision:** `python -m digest` as its own command, separate from
  triage. Digest body is a templated list grouped/sorted by urgency
  (urgent/high first), not an LLM-composed narrative. A new `digest_log`
  table (account_id, digest_date, sent_at) tracks whether today's digest
  was already sent, checked before dispatch to make retries safe.
- **Why:** Separate command means a failed send can be retried without
  re-running triage (summaries are already durably stored — cheap to
  retry the send alone). Templated list avoids an extra LLM call for a
  step that's fundamentally formatting, keeping output deterministic and
  easy to verify. digest_log prevents a retry from double-sending.
- **Alternatives considered:** LLM-composed narrative digest (more
  polished, but non-deterministic and an unnecessary cost/failure point);
  folding dispatch into the same run as triage (simpler process count,
  but complicates retry semantics).
- **Revisit if:** a narrative digest is wanted later — can layer on top
  of the templated version without changing dispatch/retry logic.

### 2026-08-04 — Summarization/triage LLM: OpenAI, not Anthropic

- **Decision:** Use OpenAI's budget-tier chat model (e.g. GPT-5 Mini) for
  Step 3's summarization + urgency grading, rather than adding a separate
  Anthropic API key.
- **Why:** Already have OpenAI billing set up for embeddings; one
  provider/one API key is simpler than two. Cost difference between
  providers is negligible at this volume either way — this was a
  simplicity call, not a cost or quality call.
- **Alternatives considered:** Anthropic API (Claude Haiku 4.5) — slightly
  more expensive per token, would need a second provider account/key.
  Considered legitimate but not worth the added complexity given no
  strong reason to need Claude specifically here.
- **Revisit if:** OpenAI's model quality proves inadequate for urgency
  judgment specifically (this is more of a nuanced-reasoning task than
  pure extraction) — Claude would be the first alternative to try.

### 2026-08-04 — Context retrieval: group by sender, not conversation thread

- **Decision:** For Step 3's "context for summary" retrieval, group past
  emails by sender address rather than Graph's conversationId (thread ID).
- **Why:** No schema change needed — sender is already captured. Good
  enough for the common case (same person/service emailing repeatedly).
- **Known limitation:** same-sender can both over-group (unrelated emails
  from a shared address like noreply@) and under-group (a reply from a
  different participant in the same thread won't be caught).
- **Revisit if:** retrieval quality testing in Step 3 shows this producing
  bad context in practice — conversationId is available from Graph and
  would need to be added to the emails table if so.

### 2026-08-04 — Digest time window: previous calendar day, midnight-to-midnight Eastern

- **Decision:** "Today's emails" = all emails with `received_at` falling within
  the previous calendar day, midnight-to-midnight in US Eastern time.
- **Why:** Matches human intuition of "yesterday" better than a rolling
  24-hour window, and doesn't require tracking last-run state the way a
  since-last-successful-run approach would.
- **Implementation note:** Eastern shifts between EST/UTC-5 and EDT/UTC-4
  across the year — must use a proper timezone-aware library (Python's
  `zoneinfo`, not a fixed UTC offset) so the boundary is correct year-round,
  not just half of it.
- **Alternatives considered:** rolling 24h from job run time (simpler, but
  doesn't match "yesterday" intuitively); since-last-successful-run (most
  accurate, but adds state-tracking complexity not worth it yet).
- **Revisit if:** the group of users spans multiple timezones later — a
  single fixed timezone stops making sense once it's not just you.

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
  `account_id` on each table. This only affects _fresh_ volumes going
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

**Status: done.** Implemented and verified end-to-end — 43 emails from the
previous-day Eastern window summarized and graded, rows confirmed durably
persisted in `email_summaries` (39 low, 2 medium, 1 high, 1 urgent). Scope
per the three 2026-08-04
decisions above (time window, sender-based context grouping, OpenAI LLM
choice). Output is populated `email_summaries` rows only — no digest
formatting or dispatch (that's step 4).

#### New package: `triage/`

```
triage/
  __init__.py
  time_window.py   # previous_day_window() — Eastern midnight-to-midnight, zoneinfo
  db.py             # target-email query, sender-context query, grounding query, upsert_summary
  llm.py             # prompt building + OpenAI chat completions call
  __main__.py         # orchestration — run via `python -m triage`
```

Mirrors `ingest/`'s one-file-per-concern shape. `triage/db.py` reuses
`ingest.db.get_connection` rather than duplicating connection setup.

#### 1. Selecting "today's" emails

```python
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

def previous_day_window(now=None):
    now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
    end = datetime.combine(now.date(), time.min, tzinfo=EASTERN)
    start = end - timedelta(days=1)
    return start, end
```

Query: `WHERE account_id = %s AND received_at >= %s AND received_at < %s`.
`received_at` is `TIMESTAMPTZ` (an absolute instant), so comparing against
tz-aware Eastern boundaries via psycopg is correct without manual UTC
conversion. Using `zoneinfo` (not a fixed offset) keeps the boundary correct
across the EST/EDT transition, per the decision's implementation note.

#### 2. Context for summary — same sender, strictly prior in time

```sql
SELECT subject, received_at, raw_body
FROM emails
WHERE account_id = %s AND sender = %s AND id != %s AND received_at < %s
ORDER BY received_at DESC
LIMIT %s
```

- Scoped by the _target_ email's own `received_at`, not the batch window —
  reaches back through all history, not just "yesterday." Matches "give the
  summarization call some history," which wouldn't mean much restricted to
  a single day.
- New config `SENDER_CONTEXT_LIMIT` (default `5`) — same defaultable-int
  pattern as `CHUNK_SIZE`.
- Context emails' bodies are truncated to a snippet (proposed: first 300
  chars, new `CONTEXT_SNIPPET_CHARS` config) rather than passed in full — a
  chatty sender's history could otherwise blow up prompt size. The _target_
  email itself always gets its full body — it's the thing actually being
  summarized.

#### 3. Grounding for urgency — pgvector similarity, strictly prior in time

Reuses the target email's own chunk embeddings (already computed in step 2
— no new embedding call needed) as the query vectors:

```sql
SELECT ec.email_id, MIN(ec.embedding <=> %s) AS distance
FROM email_chunks ec
JOIN emails e ON e.id = ec.email_id
WHERE ec.account_id = %s AND ec.email_id != %s AND e.received_at < %s
GROUP BY ec.email_id
ORDER BY distance ASC
LIMIT %s
```

Run once per chunk of the target email, merge results in Python (keep each
candidate email's best/lowest distance across all query chunks), take the
overall top `GROUNDING_LIMIT` (default `5`) distinct past emails. Cosine
distance (`<=>`) — the standard choice for OpenAI embeddings.

- **No new vector index (ivfflat/hnsw) yet** — still deferring, per the
  step-2-era decision. Dataset is ~176 chunks; a sequential-scan KNN over
  that is trivially fast. Revisit once real volume exists.
- Same "strictly prior in time" filter as sender-context, for the same
  reason: grounding "is this recurring or novel" against another
  still-unprocessed email from today's own batch doesn't reflect
  established history.

#### 4. Summarization + urgency grading — OpenAI, `gpt-5-mini`

**Recommend `gpt-5-mini`** — OpenAI's budget chat tier, matching the
"Summarization/triage LLM" decision's own example. This is a structured
extraction/classification task (summarize + classify into 4 buckets), not
one needing frontier reasoning — a budget model is appropriate, keeps cost
negligible at this volume, and reuses `OPENAI_API_KEY` (no new provider or
key).

Chat Completions endpoint with JSON mode
(`response_format: {"type": "json_object"}`) — plain `requests`, matching
the existing no-SDK style:

```python
def summarize_and_grade(target, sender_context, grounding):
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json={
            "model": config.SUMMARIZATION_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(target, sender_context, grounding)},
            ],
            "response_format": {"type": "json_object"},
        },
    )
    response.raise_for_status()
    result = json.loads(response.json()["choices"][0]["message"]["content"])
    return result["summary"], result["urgency"]
```

System prompt instructs the model to return
`{"summary": "...", "urgency": "low"|"medium"|"high"|"urgent"}`. No
Python-side validation of the returned `urgency` value beyond what the DB's
`CHECK` constraint enforces (below) — consistent with the "let it crash,
re-run is safe" failure philosophy already agreed for step 2's embedding
calls.

#### 5. Urgency representation: `TEXT` + `CHECK` constraint, not a native `ENUM`

Proposing 4 levels: `low`, `medium`, `high`, `urgent`.

- **Not a numeric score** — LLMs are reliably good at sorting into a
  handful of discrete buckets, but notoriously inconsistent at fine-grained
  numeric self-rating (a "6 vs 7 out of 10" distinction isn't meaningfully
  reproducible run to run). A small ordinal category is both more
  trustworthy and more directly useful for a digest that wants to say
  "here's what's urgent," not rank-sort by a shaky score.
- **`TEXT` + `CHECK (urgency IN (...))` over a native Postgres `ENUM`
  type** — both enforce validity at the DB layer, but a `CHECK` constraint
  is trivially redefinable (`DROP CONSTRAINT` / `ADD CONSTRAINT`) if the
  taxonomy needs tuning once step 4's digest formatting is actually built,
  whereas Postgres `ENUM` types are awkward to modify (values can be added
  but not removed without recreating the type). Favoring the
  easier-to-change option since this project is still actively iterating.

#### 6. `email_summaries` table

```sql
CREATE TABLE email_summaries (
    id           BIGSERIAL PRIMARY KEY,
    account_id   TEXT NOT NULL,
    email_id     BIGINT NOT NULL UNIQUE REFERENCES emails(id) ON DELETE CASCADE,
    summary      TEXT NOT NULL,
    urgency      TEXT NOT NULL CHECK (urgency IN ('low', 'medium', 'high', 'urgent')),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX email_summaries_account_id_idx ON email_summaries (account_id);
```

- `email_id UNIQUE` + upsert (`ON CONFLICT (email_id) DO UPDATE`) — one
  summary per email, matching the idempotent-upsert pattern already used
  for `emails`/`email_chunks`; re-running step 3 for the same day
  regenerates rather than duplicating.
- `account_id` denormalized (redundant via `email_id` → `emails.account_id`)
  — same reasoning as `email_chunks`: direct filtering without a join once
  step 4 needs "all of today's summaries for account X."
- **No `model` column** — confirmed dropped, not needed.

New `db/migrations/003_add_email_summaries.sql` (applied by hand against
the running container, same pattern as the prior two migrations) — just the
`CREATE TABLE` + index above, nothing destructive to existing tables.
`db/init/001_schema.sql` also gets the same block added, for fresh installs.

#### Config additions

```python
SUMMARIZATION_MODEL = os.environ.get("SUMMARIZATION_MODEL", "gpt-5-mini")
SENDER_CONTEXT_LIMIT = int(os.environ.get("SENDER_CONTEXT_LIMIT", "5"))
GROUNDING_LIMIT = int(os.environ.get("GROUNDING_LIMIT", "5"))
CONTEXT_SNIPPET_CHARS = int(os.environ.get("CONTEXT_SNIPPET_CHARS", "300"))
```

All optional/defaultable — no new required `.env` value; `OPENAI_API_KEY`
is reused as-is.

#### `triage/__main__.py` — orchestration

Same single-account-per-run shape as `ingest/__main__.py` (scoped by
`config.ACCOUNT_ID`, no multi-account loop — that's still deferred
OAuth/allowlist work): compute the previous-day window, fetch that window's
emails for the account, and for each one build sender-context + grounding,
call the LLM, upsert the summary.

#### Not doing

No digest formatting or dispatch (step 4). No new vector index. No
Lambda/EventBridge/RDS. No new OpenAI API key. No retry/backoff on the LLM
call (same failure philosophy as step 2 — crash is safe, re-run is
idempotent).

#### Open items (resolved)

- `model` column: dropped, confirmed.
- Default limits (`SENDER_CONTEXT_LIMIT=5`, `GROUNDING_LIMIT=5`,
  `CONTEXT_SNIPPET_CHARS=300`) and the strictly-prior-in-time retrieval
  filter: confirmed as proposed.

#### Added during implementation: retry-with-backoff on the OpenAI call

- **Decision:** `_call_openai()` in `triage/llm.py` retries up to 3 times
  (2s/4s backoff) before raising, rather than failing on the first bad
  response.
- **Why:** Not the general failure-handling work step 3's plan explicitly
  deferred to step 5 — this is narrower, reactive to a specific real issue
  hit during implementation: `gpt-5-mini` returned intermittent `404
model_not_found` errors (~40-60% failure rate) for a while after OpenAI
  org verification, which OpenAI's own error message describes as a
  propagation delay across their backend. Confirmed via repeated `curl`
  calls that success/failure was inconsistent request-to-request against
  the identical payload, not a real permanent 404.
- **Scope:** this is retry for one specific known-transient condition, not
  general resilience (no retry in `ingest/embeddings.py`'s OpenAI call, no
  handling for other failure modes). Step 5 remains the place for
  comprehensive failure handling across the whole pipeline.
- **Revisit if:** the underlying flakiness never fully resolves and 5
  retries stop being enough — would bump `MAX_RETRIES` further or
  investigate further rather than assume it's permanently fixed. (Bumped
  from 3 to 5 during implementation after 3 still wasn't reliable enough at
  the observed ~40% single-request failure rate.)

#### Bug found during implementation: silent data loss from a transaction-commit gap

- **Symptom:** `python -m triage` printed a success line for every email
  ("Summarized N (...): subject") with no errors, but `email_summaries` was
  completely empty afterward.
- **Root cause:** `ingest.db.get_connection()` doesn't set autocommit, and
  `triage/__main__.py` runs several plain `SELECT`s (`get_target_emails`,
  `sender_context`, `get_chunk_embeddings`, `similar_past_emails`) _outside_
  any `with conn.transaction():` block, interleaved with the
  `with conn.transaction():` blocks wrapping the actual `upsert_summary`
  writes. In psycopg3's default (non-autocommit) mode this left the
  connection in an ambiguous open-transaction state, and whatever wasn't
  explicitly committed was silently rolled back on `conn.close()`.
  `ingest/__main__.py` never hit this because every one of its DB touches
  is already inside a `with conn.transaction():` block — `triage` was the
  first module to mix bare reads with transactional writes on the same
  connection.
- **Fix:** `conn.autocommit = True` added to the shared
  `get_connection()` in `ingest/db.py` — psycopg3's own recommended
  pattern (autocommit for reads, explicit `with conn.transaction():` for
  atomic writes). Changes nothing observable for `ingest` (already fully
  transaction-wrapped); fixes the silent data loss in `triage`.
- **Verified:** re-ran after the fix — 43 rows durably persisted, confirmed
  via a fresh `psql` query after process exit.

### Step 4 — Daily Digest Dispatch

**Status: done.** Implemented and verified end-to-end — real digest email
sent via Graph for `stusick@outlook.com` covering 2026-08-03, `digest_log`
row confirmed, and a second run correctly skipped (no double-send) instead
of re-sending. Scope per the 2026-08-04 "Digest dispatch" decision above.
Manual-run only — no Lambda/EventBridge (step 5).

**Data quality issue found and worked around, not fixed at the data
layer:** `emails`/`email_summaries` contain a leftover `account_id` typo
(`samtusick@outlook.com`) from an earlier `.env` edit during this session,
alongside the correct `stusick@outlook.com`. The old `get_token_silent()`
bug never surfaced this (it ignored `account_id` entirely); the fix above
correctly does, and skips it safely (`Not authenticated for
samtusick@outlook.com`) rather than mis-sending. Left as-is rather than
cleaned up — harmless orphan rows, and self-resolving within a day or two
as those dates age out of the digest's previous-day window. Could be
manually deleted later if it's ever distracting.

#### New package: `digest/`

```
digest/
  __init__.py
  db.py           # get_account_ids_with_summaries, get_summaries_for_digest, already_sent, mark_sent
  formatting.py    # build_digest_html
  __main__.py       # orchestration — run via `python -m digest`
```

Plus one new function in the existing `graph/client.py`: `send_mail`. Reuses
`ingest.db.get_connection` and `triage.time_window.previous_day_window`
(same Eastern window triage used, so digest and triage always agree on
which emails count as "today's").

#### Which accounts get a digest

```sql
SELECT DISTINCT es.account_id
FROM email_summaries es
JOIN emails e ON e.id = es.email_id
WHERE e.received_at >= %s AND e.received_at < %s
```

Loops over whatever distinct `account_id`s actually have summary rows in
the window, rather than hardcoding `config.ACCOUNT_ID` — matches "for each
account_id" and sets the shape up correctly for real multi-account support
later.

**Fixed during planning, ahead of building digest:** `get_token_silent()`
used to always grab `accounts[0]` from the MSAL cache regardless of which
account was actually wanted — harmless with one account, but would have
sent one account's digest through a _different_ account's authenticated
mailbox once a second account's data existed, breaking the self-send trust
boundary. Fixed by matching on username instead:

```python
def get_token_silent(account_id):
    app, cache = build_msal_app()
    accounts = app.get_accounts()
    match = next((a for a in accounts if a.get("username") == account_id), None)
    if not match:
        return None
    result = app.acquire_token_silent(config.GRAPH_SCOPES, account=match)
    _save_cache(cache)
    return result.get("access_token") if result else None
```

Threaded through as a required parameter at all three call sites
(`app.py`, `ingest/__main__.py`, and `digest/__main__.py` below) rather than
left implicit. MSAL's cache already supports holding multiple accounts —
this was a selection bug, not a storage limitation. Verified by re-running
`python -m ingest` end-to-end after the change (exit code 0, real Graph
auth + DB writes).

**Still deferred, and this fix doesn't change that:** the cache itself is
still one local `token_cache.bin` file, which won't survive on Lambda
(step 5's ephemeral filesystem) — moving it to a durable per-account store
(e.g. a Postgres table) is separate follow-up work, not solved by the
selection fix above.

#### Digest body: simple HTML, not plain text

**Recommend HTML** over plain text — built with plain string
formatting/f-strings, no templating engine (matches "no unnecessary
abstraction"):

- **Plain text** would be simpler (zero escaping, renders identically
  everywhere) but the actual product goal (CLAUDE.md: "sends the user a
  daily digest email") wants real visual grouping by urgency, which plain
  text can only fake with indentation/labels.
- **HTML**'s real cost is that every interpolated value (subject, sender,
  summary — all arbitrary email/LLM content) must be escaped to avoid
  broken markup. That's a non-issue in practice: stdlib `html.escape()` on
  each value, no new dependency. Given the escaping cost is trivial and the
  payoff (actual headers/lists per urgency section, which Graph/Outlook
  renders natively) is real, HTML wins here.

```python
URGENCY_ORDER = ["urgent", "high", "medium", "low"]
URGENCY_LABELS = {"urgent": "Urgent", "high": "High", "medium": "Medium", "low": "Low"}

def build_digest_html(digest_date, summaries_by_urgency):
    sections = []
    for level in URGENCY_ORDER:
        items = summaries_by_urgency.get(level, [])
        if not items:
            continue
        rows = "".join(
            f"<li><strong>{html.escape(item['subject'] or '(no subject)')}</strong> "
            f"— {html.escape(item['summary'])} "
            f"<span style=\"color:#666\">({html.escape(item['sender'] or '')})</span></li>"
            for item in items
        )
        sections.append(f"<h3>{URGENCY_LABELS[level]}</h3><ul>{rows}</ul>")
    return f"<h2>Daily Digest — {digest_date.isoformat()}</h2>" + "".join(sections)
```

Sections ordered urgent → high → medium → low; empty sections omitted
entirely rather than printed as "none." Within a section, emails are
ordered by `received_at` (chronological), matching triage's own ordering.

#### `digest_log` table — tracks confirmed sends only

```sql
CREATE TABLE digest_log (
    id           BIGSERIAL PRIMARY KEY,
    account_id   TEXT NOT NULL,
    digest_date  DATE NOT NULL,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, digest_date)
);
```

- **No "pending/attempted" row inserted before sending** — a row only ever
  gets inserted _after_ a confirmed successful Graph send. This directly
  satisfies "only mark sent on confirmed successful send" without needing
  a separate status column: existence of the row means sent, full stop.
- Pre-send check (`SELECT 1 FROM digest_log WHERE account_id = %s AND
digest_date = %s`) gates the whole per-account block — if a digest was
  already sent for that account/date, skip entirely (no re-fetch, no
  re-send). This is what makes retrying the whole `python -m digest`
  command safe: already-sent accounts are skipped, not-yet-sent accounts
  are attempted again.
- `UNIQUE (account_id, digest_date)` is a defensive backstop against a
  genuine double-insert (e.g. concurrent runs) — not expected to ever fire
  in this step's manual-run-only scope, but cheap to have and consistent
  with letting a real conflict raise loudly rather than silently swallowing
  it.
- `digest_date` is `DATE`, not `TIMESTAMPTZ` — it's a calendar-day label
  (`window_start.date()` from the same Eastern window triage used), not an
  instant.

New `db/migrations/004_add_digest_log.sql` (applied by hand against the
running container, same pattern as the prior three migrations) — just the
`CREATE TABLE` above. `db/init/001_schema.sql` gets the same block added,
for fresh installs.

#### `graph/client.py` — new `send_mail`, with retry baked in

Same retry-with-backoff shape as `triage/llm.py`'s `_call_openai` (retry
baked directly into the function making the call, not split into a
separate "client" + "retry wrapper" layer — matches the one precedent this
codebase already has for retry):

```python
GRAPH_SEND_MAX_RETRIES = 3
GRAPH_SEND_RETRY_BACKOFF_SECONDS = 2

def send_mail(access_token, to_address, subject, html_body):
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
        }
    }
    response = None
    for attempt in range(GRAPH_SEND_MAX_RETRIES):
        response = requests.post(
            f"{config.GRAPH_BASE_URL}/me/sendMail",
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
        )
        if response.ok:
            return
        if attempt < GRAPH_SEND_MAX_RETRIES - 1:
            time.sleep(GRAPH_SEND_RETRY_BACKOFF_SECONDS * (2**attempt))
    response.raise_for_status()
```

3 attempts (2s/4s backoff) — a general prophylactic default for transient
network/Graph hiccups, not tuned against any specific known issue the way
triage's 5 retries were tuned against the observed OpenAI verification
flakiness. `to_address` is `config.ACCOUNT_ID` (or the discovered
`account_id`, same value) — self-send only, matches the existing "self-send
digest, not general auto-send" decision; no new Graph scope needed
(`Mail.Send` already granted).

#### `digest/__main__.py` — orchestration

For each account discovered in the window: skip if already sent
(`digest_log` check) → get a token → fetch that account's grouped summaries
→ build the HTML body → `send_mail` (retries internally, raises if all
attempts fail) → only on successful return, `mark_sent`. A send failure
(all retries exhausted) crashes the run for that account — consistent with
the established "let it crash, safe to rerun" philosophy — without ever
re-touching `triage` or marking `digest_log`, exactly as specified.

#### Not doing

No Lambda/EventBridge/scheduling (step 5). No LLM-composed narrative body
(templated list only, per the decision). No new Graph scope. No
per-account token storage (flagged gap above, deferred with the rest of
multi-account OAuth work).

#### Open items — flagging rather than deciding silently

1. HTML vs plain text — recommended HTML above with reasoning; confirm or
   push back.
2. Digest email subject line — proposed `"Daily Digest — {digest_date}"`.
3. The account/token gap above — real limitation, not blocking for a
   single-account run today.

### Step 5 — Automation + Guardrails

_(not started)_
