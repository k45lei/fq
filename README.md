# FutureQuest

FutureQuest helps students explore their interests, strengths, and future goals through a fixed questionnaire. Students can start as guests or optionally sign in with Google.

The questionnaire has three stages: **interests**, **strengths**, and **EQ**. Interests is free; one Stripe payment unlocks the remaining stages for that attempt. Each completed stage stores its answers and a cumulative snapshot of all answers submitted so far.

**Results currently show placeholder text.** No LLM or report-generation service runs. A protected API can accept externally supplied results later. PDF downloads, email delivery, and discounts are not implemented in this phase.

## Stack and layout

| Component | Location | Technology |
| --- | --- | --- |
| Web app | `frontend/` | React, TypeScript, Vite, Tailwind CSS |
| API | `backend-ts/src/` | Fastify, TypeScript |
| Database schema and migrations | `backend-ts/prisma/` | Prisma, PostgreSQL 18 |
| Local database | `backend-ts/compose.yml` | Docker Compose |
| Worker entrypoint | `backend-ts/src/worker.ts` | Separate Node process; currently idle |

The API runs on port **8000**, the frontend on **5173**, and the local database on **5433**. The frontend development server proxies `/api` requests to the API.

Use [the Prisma schema](backend-ts/prisma/schema.prisma) and [the backend guide](backend-ts/README.md) for implementation details. Older documents under `docs/` describe earlier designs and may differ from the current code.

## Local setup

You need Node.js **22.12 or newer**, npm, pnpm, and Docker with Compose. The frontend specifies its pnpm version in [package.json](frontend/package.json).

Run npm commands from `backend-ts/` and pnpm commands from `frontend/`. There is no root Node package.

### 1. Install backend dependencies

From the repository root:

```bash
cd backend-ts
npm ci
npm run db:generate
```

### 2. Configure the API

For the first setup, create a local environment file:

```bash
cp .env.example .env
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

Copy the generated value into `backend-ts/.env` as `SESSION_KEY`. It must be exactly **64 hexadecimal characters**. The command prints the key; it does not update the file. Keep an existing `.env` when restarting the app.

The supplied defaults are:

```dotenv
DATABASE_URL=postgresql://futurequest:futurequest@localhost:5433/futurequest_ts
APP_ORIGIN=http://localhost:5173
PORT=8000
NODE_ENV=development
```

Google, Stripe, and result-publication credentials can remain blank for local work on the free questionnaire flow. Their corresponding integrations will be unavailable until configured.

### 3. Start PostgreSQL and initialize the database

Still inside `backend-ts/`:

```bash
docker compose up -d --wait
npm run db:migrate
```

Use `backend-ts/compose.yml`; the root `docker-compose.yml` belongs to the previous backend. The new database is `futurequest_ts` on port **5433**, with a dedicated volume. These migrations are for a fresh database, not an upgrade of the previous Python database.

**Seed dependency:** [the seed loader](backend-ts/src/seed.ts) still expects `backend/seeds/core-instrument-v2.yaml`. That file is currently missing following removal of the old backend directory. Before seeding a fresh database, restore the questionnaire YAML or move it into `backend-ts/` and update the loader's path. Seeding and backend integration tests depend on that file.

Once the questionnaire source is available:

```bash
npm run db:seed
```

Seeding loads 40 questions. Running it again with unchanged content is safe; it refuses to change question content once attempts exist.

### 4. Start the API

Inside `backend-ts/`:

```bash
npm run dev
```

Leave this terminal running. In another terminal, check database connectivity through the API:

```bash
curl http://localhost:8000/health
```

A healthy response includes `"status":"ok"`.

### 5. Start the frontend

Open another terminal at the repository root:

```bash
cd frontend
pnpm install
pnpm dev
```

Open **http://localhost:5173**. Keep PostgreSQL and the API running alongside the frontend. `APP_ORIGIN` must match the browser-facing origin if you use a different address.

The worker is not required for the current flow. `npm run worker` starts a separate process that checks database connectivity and stays idle; it does not generate results.

## Optional integrations

Configure these in `backend-ts/.env`, then restart the API.

| Integration | Variables | Purpose |
| --- | --- | --- |
| Google sign-in | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional student authentication |
| Stripe | `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET` | One payment unlocks the remaining questionnaire stages |
| Result publication | `RESULT_API_SECRET` | Authorizes an external service to publish a stored report result |

For local Google sign-in, register this callback:

```text
http://localhost:5173/api/auth/google/callback
```

Use an active, one-time Stripe price. The webhook endpoint is `/api/stripe/webhook`. See [the backend guide](backend-ts/README.md#google-and-payments) for event configuration and refund behavior.

Externally supplied results use `PUT /api/integrations/reports/:reportId/result` with a bearer secret. See [the publication contract](backend-ts/README.md#protected-result-publication). Without a publisher, reports remain pending and display the placeholder indefinitely.

## Checks and builds

Inside `backend-ts/`:

```bash
npm run typecheck
npm test
npm run build
```

The integration tests start a disposable PostgreSQL instance. They do not use your application database or call live Google or Stripe services. The questionnaire seed file described above must be present.

Inside `frontend/`:

```bash
pnpm typecheck
pnpm test
pnpm build
```

The backend build is written to `backend-ts/dist/`; run it with `npm start` from `backend-ts/`. The frontend build is written to `frontend/dist/`. Production hosting must route `/api` to the API and serve the frontend with client-side routing support.

## Common setup issues

| Symptom | What to check |
| --- | --- |
| `npm ci` cannot find a lockfile, or npm cannot find `package.json` | Run the command inside `backend-ts/`, not the repository root. |
| `docker compose` is unknown | Install the Compose plugin and confirm `docker compose version` works. |
| Permission denied on `/var/run/docker.sock` | With system Docker Engine, run `sudo docker compose up -d --wait`. Keep npm and pnpm commands under your normal user. |
| Prisma `P1001` or `ECONNREFUSED` on port 5433 | Start the database and inspect `docker compose ps` and `docker compose logs postgres` from `backend-ts/`. |
| `SESSION_KEY must be 32 bytes encoded as hex` | Save a generated 64-character hexadecimal key in `.env` and restart the API. |
| Seeding fails with a missing questionnaire file | Restore or relocate the YAML source referenced by `backend-ts/src/seed.ts`. |
| The frontend loads but API requests fail | Confirm the API is running on port 8000 and `APP_ORIGIN` matches the frontend URL. |
