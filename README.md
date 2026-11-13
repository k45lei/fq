# Current backend: Fastify + Prisma

The new local backend lives in [backend-ts](backend-ts/README.md), with a fresh PostgreSQL database, guest/Google authentication, Stripe access, and pending results. Start with that guide. The frontend uses the same port 8000 API proxy. Report generation, PDFs, emails, and discounts are deferred.

The Python backend and the instructions below are retained as legacy reference; they do not describe the new backend.

#go to profile > API Tokens > Create Token > Edit zone DNS |Use Template| > 
# Permissions: 
# {
# 	Zone | DNS | Edit
# 	Account | Cloudflare Tunnel | Edit
# }# FutureQuest

Career-path guidance for teenagers and young adults. Users complete a questionnaire, and the
results are compiled into a report covering their career traits, strengths, suggested careers and
next steps — shown in-app, saveable as a PDF, and emailed as a summary.

> **Status: early scaffolding.** The backend project and local database are set up. There is no
> application server, no migrations and no frontend yet. See [Roadmap](#roadmap).

## Layout

```
.
├── CLAUDE.md              Project context for Claude Code sessions
├── docker-compose.yml     Local Postgres
├── docs/
│   ├── requirements.md    Product requirements and stack
│   └── schema.md          Full database schema, with rationale
├── backend/               FastAPI API + report worker (Python)
│   ├── pyproject.toml
│   ├── .env.example
│   └── app/
└── frontend/              React app (not created yet)
```

## Prerequisites

- **Docker** — runs Postgres locally. Nothing else needs it yet.
- **[uv](https://docs.astral.sh/uv/)** — Python packaging. It can install Python itself, so you
  don't need a system Python 3.12+: `uv python install 3.12`.
- **Node + pnpm** — for the browser app.
- **cairo and pango** — WeasyPrint needs them to render PDFs. Present on most Linux desktops;
  on Debian/Ubuntu it's `apt install libpango-1.0-0 libpangoft2-1.0-0`. Without them every
  other feature works and the PDF route returns a 503 saying exactly this.

## Getting started

### 1. Start Postgres

From the repository root:

```bash
docker compose up -d postgres
```

Postgres 18, listening on `localhost:5432`, with database/user/password all `futurequest`. Data
lives in a named Docker volume and survives `docker compose down`.

### 2. Configure the backend

```bash
cd backend
cp .env.example .env
```

Generate a session secret and paste it into `.env` as `SESSION_SECRET`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

**This one is required** — the app won't start without it. Ownership cookies are signed with
it, and without a stable secret every returning visitor would look like a stranger.

The integration keys (Google, Stripe, Resend) can stay blank — the app starts without them and
those features are simply unavailable.

### 3. Install dependencies

```bash
uv sync
```

This creates `backend/.venv`, installs everything, and writes `uv.lock`. Commit the lockfile.

### 4. Apply migrations

```bash
uv run alembic upgrade head
```

### 5. Seed content

```bash
uv run python -m app.seed_careers seeds/career-families.yaml
uv run python -m app.seed_pay seeds/career-pay-mock.yaml
uv run python -m app.seed_guidance seeds/career-guidance.yaml
uv run python -m app.seed seeds/core-instrument-v2.yaml --activate
```

`career-pay-mock.yaml` holds **placeholder figures, not sourced data** — they exist so the demo
looks complete. They're stored with `source: mock`, which the report carries through so the
interface can label them "indicative only" rather than presenting them as researched. Replace
with BLS/OEWS figures (`source: bls_oews`, a real `source_url` and the release's `as_of`) before
anyone makes a decision on them. Where both exist for a career, the sourced figure wins.

Career families are the closed vocabulary reports rank against. That seed upserts on `slug`, so
it's safe to re-run after editing the file. Never delete a family a report might reference — set
`is_active: false` instead.

Questionnaire content is authored in YAML under `seeds/` until there's an admin UI. The file is
validated before anything is written, so a content mistake names the offending question rather
than surfacing as a constraint violation.

Omit `--publish` while iterating — publishing is one-way and freezes the questionnaire. Editing
published content is impossible by design; bump `version` and seed again instead.

**Publishing and activating are separate.** Publishing freezes a version's content; activating
makes it the one users are served. `--activate` implies `--publish`, since activating a draft is
unrepresentable. That separation is what lets a new version be staged and reviewed before it goes
live — and rolling back is just activating the previous version again, since activations are
append-only.

### 6. Check it works

```bash
uv run uvicorn app.main:app --reload
curl localhost:8000/health
```

`{"status":"ok","environment":"development"}` means the app is up *and* reached the database —
the endpoint issues a real query, so a reachable app with an unreachable database fails here.

To inspect settings without starting anything:

```bash
uv run python -c "from app.config import get_settings; print(get_settings())"
```

Safe to paste anywhere — secrets render as `SecretStr('**********')`.

### 7. Run the browser app

```bash
cd frontend
pnpm install
pnpm dev
```

Then open http://localhost:5173 with the backend running. The dev server proxies `/api` to
FastAPI, so the browser sees a single origin and CORS doesn't come into it during development.

Architecture is layered — views → controllers (`api/queries.ts`, `store/`) → the API layer
(`api/client.ts`, `api/types.ts`). **Nothing outside `src/api/` calls `fetch` or knows a URL.**
See CLAUDE.md.

## Common commands

All backend commands run from `backend/`.

| Command | What it does |
| --- | --- |
| `uv sync` | Install/update dependencies to match `uv.lock` |
| `uv sync --no-dev` | Production install — skips pytest and ruff |
| `uv add <package>` | Add a dependency and update the lockfile |
| `uv run <cmd>` | Run a command inside the project environment |
| `uv run uvicorn app.main:app --reload` | Run the API with reload, on http://127.0.0.1:8000 |
| `uv run python -m app.seed <file.yaml> [--publish]` | Load a questionnaire from YAML |
| `uv run python -m app.seed_careers <file.yaml>` | Load career families (upserts, re-runnable) |
| `uv run python -m app.seed_pay <file.yaml>` | Load career pay figures (upserts, re-runnable) |
| `uv run python -m app.seed_guidance <file.yaml>` | Load per-career next steps (upserts, re-runnable) |
| `uv run pytest` | Run the tests |
| `uv run ruff check --fix` | Lint and autofix |
| `uv run ruff format` | Format |
| `uv run alembic current` | Show the currently applied revision |
| `uv run alembic upgrade head` | Apply all pending migrations |
| `uv run alembic downgrade -1` | Roll back one migration |
| `uv run alembic revision -m "what it does"` | Create an empty migration to write by hand |
| `uv run alembic upgrade head --sql` | Print the SQL instead of running it |

From the repository root:

| Command | What it does |
| --- | --- |
| `docker compose up -d postgres` | Start the database |
| `docker compose stop postgres` | Stop it, keeping data |
| `docker compose down -v` | Stop and **delete all data** |
| `docker compose exec postgres psql -U futurequest` | Open a psql shell |

## Notes

### You don't need to activate the virtualenv

uv creates and manages `backend/.venv` for you, and `uv run <cmd>` executes inside it. That's the
normal way to work — activating is optional. If you'd rather:

```bash
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### Tests need a running Postgres

`uv run pytest` creates its own database — the dev one with a `_test` suffix — drops and
re-migrates it once per session, and rolls back a transaction around each test. Your development
data is never touched, and the fixture refuses to run unless the target database name ends in
`_test`.

There is no SQLite fallback and there won't be. Enums, arrays, CHECK constraints, plpgsql triggers
and deferrable unique constraints carry most of this schema's correctness, and SQLite has none of
them — so a test that avoided Postgres would exercise almost nothing.

### The frontend has tests too

`frontend/scripts/test.sh` (or `npm test` in `frontend/`) runs them. They use Node's built-in
test runner, with `rolldown` — the bundler Vite 8 already uses — transpiling the TypeScript
first, because this Node build has no `--experimental-strip-types` support.

No test framework was added. The assertions are about URL construction, request payloads and
error handling in `src/api/client.ts` — the layer that is allowed to know about `fetch` and
URLs, and therefore the layer where a mistake would otherwise go unnoticed until a purchase
failed in front of a person.

```bash
cd frontend && npm test          # or ./scripts/test.sh
```

### Google sign-in is optional

Without `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, the login route returns 503 and
everything else works — questionnaire, reports and PDF all run on the ownership cookie alone.
Sign-in adds account-level access: your reports from any device.

#### Setting up the OAuth client

In the [Google Cloud console](https://console.cloud.google.com). The UI gets reorganised
periodically — recent versions group this under **Google Auth Platform** — so these are the
steps by name rather than by menu path.

1. **Create or pick a project.** Anything will do; it only scopes the credentials.

2. **Configure the consent screen.** User type **External** (Internal only exists for Google
   Workspace organisations). Fill in app name, a support email and a developer contact — all
   required before you can create credentials.

3. **Add scopes.** Written in full — abbreviated forms are rejected as invalid:

   ```
   openid
   https://www.googleapis.com/auth/userinfo.email
   https://www.googleapis.com/auth/userinfo.profile
   ```

   Only `openid` is a bare string; the other two are complete URLs. All three are
   non-sensitive, so the app never needs Google's verification review. They also appear in the
   console's scope picker, where ticking them is less error-prone than typing.

4. **Add yourself as a test user.** While publishing status is *Testing*, only listed test
   users can sign in — everyone else gets `access_denied`. **This is the single most common
   reason sign-in "doesn't work".** Up to 100 users, which is plenty for a demo.

5. **Create credentials → OAuth client ID → Web application.**

   - *Authorised JavaScript origins:* `http://localhost:5173`
   - *Authorised redirect URIs:* `http://localhost:5173/api/auth/google/callback`

   **One of each per entry in `ALLOWED_ORIGINS`** — see [Serving it from somewhere
   else](#serving-it-from-somewhere-else) below. Google validates the redirect URI against its
   own list, so an origin the app knows about but Google doesn't gives `redirect_uri_mismatch`.
   The error page names the URI it received, which is the quickest way to spot a difference;
   matching is exact, down to the trailing slash.

   Note the port is **5173, not 8000**. The browser talks to the Vite dev server, which
   proxies `/api` to the backend; Google redirects the browser, so it must be an address the
   browser can reach, not the backend's own `127.0.0.1:8000`.

6. **Copy the client ID and secret into `backend/.env`:**

   ```
   GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=...
   ```

   There's no `GOOGLE_REDIRECT_URI`: the callback is `<origin>/api/auth/google/callback` for
   whichever `ALLOWED_ORIGINS` entry the request matches.

   Restart uvicorn — settings are read once per process.

Google permits plain `http` for `localhost`, so no certificate is needed in development.

#### Serving it from somewhere else

A tunnel, a staging box, a teammate's machine — anywhere that isn't `localhost:5173`. Three
lists have to agree, and each one fails differently, which is the confusing part: Vite refuses
the page outright, the backend quietly redirects to localhost, and Google shows an error page.

1. **Vite** must answer to the hostname. `vite.config.ts` already allows `.trycloudflare.com`,
   `.ngrok-free.app` and `.ngrok.app` — a leading dot matches every subdomain, so a quick
   tunnel works without editing anything. For another host, set `DEV_ALLOWED_HOSTS`:

   ```bash
   DEV_ALLOWED_HOSTS=.example.com,staging.internal pnpm dev
   ```

   Without this: *"Blocked request. This host is not allowed."* The check is Vite's
   DNS-rebinding protection, and it is worth keeping rather than setting `allowedHosts: true`
   — while the dev server is running, any site that can resolve a hostname to your machine
   could otherwise read from it.

2. **The backend** needs the origin in `ALLOWED_ORIGINS` in `backend/.env`, comma-separated:

   ```
   ALLOWED_ORIGINS=http://localhost:5173,https://quest.example.ngrok.app
   ```

   Note `https` — the tunnel terminates TLS, so the browser's origin is the `https` one even
   though Vite itself is serving plain `http`. Without this, `/api/auth/google/login` returns
   **503 with a message naming the host to add**. It refuses rather than falling back, because
   a callback URI on a different host than the browser is on means the OAuth state cookie
   isn't there when Google returns — which used to show up as an opaque 500 on the callback,
   several steps from the cause.

3. **Google** needs the same origin in the console — as an authorised JavaScript origin, and
   with `/api/auth/google/callback` appended as an authorised redirect URI. Without this:
   `redirect_uri_mismatch`, and the error page names the URI it was sent.

Then restart uvicorn; Vite picks its config up on restart too.

A quick Cloudflare tunnel gets a new random hostname each time, so steps 2 and 3 have to be
redone per tunnel. A named tunnel, or `ngrok` with a reserved domain, is worth the setup if
you're demoing sign-in more than once. Everything that *isn't* sign-in works on a quick tunnel
with only step 1.

Nothing else changes. CORS, the OAuth redirect URI and where sign-in returns to are all
derived per request: `app/origins.py` matches the incoming request against the list and uses
the entry that fits, so one deployment can serve several origins at once. A request matching
none of them falls back to the **first** entry, which is also what report emails link to —
they're sent from a background task with no request to go on, so put the address you most want
people to receive first.

Two things are deliberate here. Unlisted origins are never used, however convincing the
headers look — otherwise a forged `X-Forwarded-Host` would redirect sign-in, authorization
code and all, to whatever host it named. And the Vite proxy does **not** set `changeOrigin`,
because that would rewrite `Host` to `127.0.0.1:8000` and leave the backend unable to tell
which origin the browser is on.

#### Checking it works

```bash
curl -si localhost:8000/api/auth/google/login | head -1
```

`307` means configured; `503` means the credentials aren't being read. Then visit
`http://localhost:5173/api/auth/google/login` in a browser for the real round trip, and
`curl` the `/api/auth/me` endpoint afterwards to see the session.

### The paywall

The entitlement gate reads the `entitlements` table server-side; see `docs/schema.md` and
`app/entitlements.py`. There is no setting that can widen it, and there never should be — a
visitor must not be able to turn the paywall off.

With no active entitlement, `GET /api/questionnaire` returns only the interests section, and
submitting any other section returns 403. A `single_report` entitlement (one response, no
expiry) or a `window` entitlement (any response, time-bounded) unlocks all three.

The gate asks about the **response's owner**, not the caller, and a `single_report` names one
response — so the questionnaire preview needs to know which response it's about:
`GET /api/questionnaire?response_id=...` widens the preview for that response, and without
it the free tier is served even to a visitor who has paid.

### Stripe

Buying is a Stripe Checkout Session. Three settings, all optional — the app starts without
any of them, the paywall still holds, and the routes that need Stripe return 503 naming the
missing one:

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PRICE_ID=price_...
STRIPE_WEBHOOK_SECRET=whsec_...
REPORT_PRICE_CENTS=2999
REPORT_PRICE_CURRENCY=usd
```

**`STRIPE_PRICE_ID` is authoritative.** The Price is what the customer is actually charged.
`REPORT_PRICE_CENTS` is what the paywall *displays*, and the two must match — a mismatch is a
display bug, not a charging one. Create the Price in the Stripe dashboard and paste its id
here.

**`STRIPE_WEBHOOK_SECRET` is not the secret key.** It's the signing secret of the endpoint
that delivers events to `/api/stripe/webhook`, found under *Webhooks → your endpoint →
Signing secret*. Using the wrong one fails every delivery, and the symptom is subtle: a paid
purchase that never grants access, with no error the customer can see.

Register the endpoint for `checkout.session.completed`,
`checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed` and
`charge.refunded`. The first three are what grant access; the last is what revokes it.

#### Checking it's hooked up

```bash
cd backend && uv run python -m app.stripe_check
```

Four layers, so a failure says which one is broken: are the settings there, does Stripe
accept the key, does the Price exist and does it match what the paywall displays, and is a
signing secret set that has an endpoint pointing at us. It only reads — nothing is charged
and no row is written — and it exits 1 if anything is wrong.

It matters because the failure modes otherwise look alike. A typo'd key, a revoked key, a
Price from the other mode and a signing secret that doesn't match the endpoint all present
as "the paywall still holds", and the last of those is the worst: the money goes through
and nothing unlocks.

#### Testing it locally

`stripe listen` forwards live test-mode events to a local endpoint, and prints a signing
secret of its own:

```bash
stripe listen --forward-to localhost:8000/api/stripe/webhook
```

Put the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET` and restart uvicorn. Then pay with
a test card — `4242 4242 4242 4242`, any future expiry, any CVC — and check that one
`purchases` row and one `entitlements` row appeared.

`stripe listen` only forwards events it can see, so a webhook registered in the dashboard is
*also* delivering to Stripe's servers. That's fine in test mode, and the local endpoint
verifies the same signature either way.

The CLI itself: <https://stripe.com/docs/stripe-cli>. It needs an account before any of this
works — `stripe login` opens a browser to authorise it, or `stripe login --api-key <key>`
takes a key directly. Without a login, `stripe listen` has nothing to forward.

#### How a purchase becomes access

The webhook is the authority: it verifies the raw signed body, then writes the purchase and
the entitlement in one transaction. The browser's return doesn't wait for it —
`GET /api/checkout/{session_id}` asks the server whether access exists yet, and if the webhook
is behind, performs the same grant inline. One implementation, two callers, so a visitor who
has just paid is never refused because a background delivery was slow.

A refund, issued from the Stripe dashboard, revokes access: it stamps both
`purchases.refunded_at` and the entitlement's `revoked_at`.

#### What happens after the payment

**Payment unlocks the questionnaire; it doesn't produce a report.** A report is scored from
the answers, so a full report can't exist until the paid sections have been answered. The
return page sends the visitor into the questionnaire, and the report appears when they
finish.

A section can only be submitted once, and the server refuses a second attempt with a 422.
`GET /api/questionnaire?response_id=...` reports the sections a response has already had
submitted, so the client offers only the ones left — a visitor who finished the free tier,
paid, and came back answers the two they haven't rather than resubmitting the one they did.

See `docs/plan.md` for the full design, and `CLAUDE.md` for the rules that are easy to undo by
accident.

### Migrations are written by hand

`alembic revision` creates an empty migration deliberately — there is no `--autogenerate` here,
and `target_metadata` is `None` in `alembic/env.py`.

Autogenerate compares SQLAlchemy models against the database and cannot see triggers, plpgsql
functions, functional indexes or CHECK constraints. Most of this schema's integrity lives in
exactly those, so a generated migration would quietly omit them. Write the SQL.

The database URL comes from `app.config`, not `alembic.ini`, so there's one source of truth and
no password in a tracked file.

Migrations also run **synchronously, under `psycopg`** rather than the app's `asyncpg` — `env.py`
swaps the driver in the URL and leaves everything else alone. asyncpg sends every statement as a
prepared statement, and a prepared statement can hold only one command, so multi-statement DDL
blocks are impossible under it. The app itself is unaffected and still uses asyncpg.

### Postgres version

Pinned to major version 18 in `docker-compose.yml`. Major versions have incompatible on-disk
formats, so changing it means recreating the volume (`docker compose down -v`) — fine while there
are no migrations to preserve. It should eventually match whatever managed Postgres runs in
production.

The volume mounts at `/var/lib/postgresql`, **not** `/var/lib/postgresql/data`. Since Postgres 18
the image writes to a major-version subdirectory beneath that mount, which is what allows a future
`pg_upgrade --link` to run without crossing a mount boundary. Using the older `.../data` path makes
the image warn that the volume is unused, and the server comes up with an empty database.

### Design docs

`docs/requirements.md` and `docs/schema.md` are the source of truth for design decisions. The
schema doc in particular records *why* each table looks the way it does — worth reading before
changing anything database-shaped, since several constraints look removable until you know what
they're preventing.

## Roadmap

- [x] Backend skeleton — project, settings, local Postgres
- [x] Alembic + initial migration (schema, triggers, `fork_questionnaire()`)
- [x] Schema tests — fork equality, immutability triggers, composite-FK constraints
- [x] Async database wiring + `/health`
- [x] SQLAlchemy models + drift test
- [x] Seed questionnaires and career families
- [x] Questionnaire activation — publish and activate as separate decisions
- [x] Deterministic scoring and report generation
- [x] Public flow — capture, serve, submit, read the report
- [x] Frontend — capture form, questionnaire runner, report screen, paywall teaser
- [x] Google sign-in — a verified identity session, and account-level access
- [x] Report email (Resend) and PDF export
- [x] The entitlement gate — paid sections require an active entitlement, server-side
- [x] Stripe — checkout, webhook, refunds, and the frontend's pay-to-unlock flow
- [ ] LLM-generated narrative, fed by `build_transcript()`
- [ ] The admin surface — author, publish, fork, activate
- [ ] A durable job queue. Report email is on `BackgroundTasks` and is not durable today

Reports are produced deterministically from the answers — see `app/report_copy.py` for the
layer a model would replace.
