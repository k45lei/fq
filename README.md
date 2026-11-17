# FutureQuest

FutureQuest helps students explore interests, strengths, and emotional intelligence. A React frontend talks to a Fastify/TypeScript API backed by Prisma and PostgreSQL. Reports are calculated deterministically in the backend when a stage is submitted; there is no LLM or external report service.

The questionnaire has three ordered stages:

| Stage | Questions | Access | Result |
| --- | ---: | --- | --- |
| Interests | 32 | Free | Six-trait profile, abilities, values, eight career matches |
| Strengths | 76 | One paid unlock per attempt | Work style, motivators, environment preferences, career matches |
| EQ | 30 | Included in the same unlock | Five EQ domains and the combined Compass report |

Students can start as guests and optionally sign in with Google. Drafts stay in browser storage. Completed stages, cumulative answer snapshots, and reports are stored on the server. A new attempt starts free and does not inherit an earlier attempt's payment.

The interface follows `frontend/example_frontend/`. Original HTML/CSS references remain available. Shared styles live in `frontend/src/app.css`; page-specific reference styles live in `frontend/src/mockups.css`. The start form accepts optional school text. Quiz selections stay on the current question until Next is selected; Submit answers completes the stage.

## Local setup

Use Node 24+, npm, pnpm 12.5.1, and Docker Compose. There is no root Node package. Run npm in `backend` and pnpm in `frontend`.

```sh
cd backend
npm ci
npm run db:generate
cp .env.example .env
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

Set `SESSION_KEY` in `.env` to the printed 64-character hex value. Preserve an existing `.env`. The default local database URL is `postgresql://futurequest:futurequest@localhost:5433/futurequest`, and `APP_ORIGIN` is `http://localhost:5173`.

```sh
docker compose up -d --wait
npm run db:migrate
npm run db:seed
npm run dev
```

In another terminal:

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Open `http://localhost:5173`. Vite forwards `/api` to `http://127.0.0.1:8000`. Change the target with `API_PROXY_TARGET` if the local API uses another address. Set backend `APP_ORIGIN` to the exact frontend origin when changing the hostname or port.

Root `docker-compose.yml` and `backend/compose.yml` describe the same local database. Both retain project name `backend-ts` and volume `backend-ts_futurequest_ts_data` for compatibility with the directory rename. If you previously used a custom Compose project, set `FUTUREQUEST_DB_VOLUME` to that existing volume's name before starting. Do not use `down -v` to resolve setup problems. The older root volume is not removed or imported.

Seeding reads checked-in data from `backend/src/scoring/reference.json`; it has no dependency on the removed Python backend. Unchanged reseeding is safe. If a database contains attempts from a different instrument, seeding refuses to replace its questions. Use a fresh database for this release; do not reset a database containing data you need. Existing reports remain readable, but unfinished attempts using an older instrument cannot receive new deterministic results.

## Google and Stripe

Configure `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, registering:

```text
<APP_ORIGIN>/api/auth/google/callback
```

For payments set `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, and `STRIPE_WEBHOOK_SECRET`. The price must be active and one-time. `/api/pricing` retrieves it from Stripe; the application does not hardcode a price or currency. Register `<APP_ORIGIN>/api/stripe/webhook` for `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, and `charge.refunded`.

Full refunds revoke future paid submissions; partial refunds preserve access. Saved answers and reports remain readable. Webhook delivery and browser-return verification share the same idempotent fulfillment path.

## Checks

```sh
cd backend
npm run typecheck
npm test
npm run build
```

```sh
cd frontend
pnpm typecheck
pnpm test
pnpm build
```

Backend tests start disposable PostgreSQL, apply every migration, and never use the application database. They need permission to open local sockets. Google and Stripe are mocked except for local signature verification. Scoring fixtures were captured from the supplied reference implementation.

See the [verification record](docs/verification.md) for results, the repeatable browser journey, and outstanding release checks.

## Production

See [deployment instructions](deploy/README.md) for the HTTPS container setup, environment configuration, migrations, backups, and provider verification. The API runs compiled JavaScript with `npm start`; frontend assets are served by Caddy, not Vite. The separate worker is intentionally idle because report calculations run synchronously.

Production startup requires HTTPS and Google/Stripe configuration. No hosting deployment or live payment is performed by the implementation. Passing local tests does not establish that a particular provider account, callback, webhook, DNS record, or backup policy is correctly configured.

## Results and limitations

The reference instrument and scoring rules are documented in [the backend guide](backend/README.md). Confidence describes agreement between the three scores, not a probability of career success or a psychometrically validated assessment. Reference salary, outlook, and qualification claims are omitted because the supplied material has no supporting sources. Career descriptions, subjects, and activities are exploration suggestions.

PDF exports, email delivery, public sharing, discounts, school/event management, and additional roles are not implemented. The protected publication endpoint remains available for older pending reports; local ready results cannot be replaced. See [AGENTS.md](AGENTS.md) for coding conventions and project invariants.
