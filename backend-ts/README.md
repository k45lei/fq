# FutureQuest TypeScript backend

Fastify + Prisma + PostgreSQL, with a separate idle worker entrypoint. This is the new local backend; the Python application and its database are retained unchanged. No existing data is imported.

## Run locally

Use Node 22.12+ (Node 24 LTS recommended), npm, Docker, and the existing frontend's pnpm.

From `backend-ts`:

```sh
npm ci
npm run db:generate
docker compose up -d
cp .env.example .env
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

Put the generated value in `.env` as `SESSION_KEY`. The supplied database URL points to a **new** database on port **5433**, with its own Compose volume. Never substitute the Python database URL. Set `APP_ORIGIN` to the browser-facing origin, for example `http://localhost:5173`.

```sh
npm run db:migrate
npm run db:seed
npm run dev
```

Then run `pnpm dev` in `frontend`. Its existing `/api` proxy targets port 8000. Stop the Python API first if it occupies that port. The worker can be started separately with `npm run worker`; it verifies database connectivity and stays idle because no generation or delivery jobs are enabled.

`npm run build` produces `dist`; `npm start` runs the compiled API. Generate the Prisma client before building. The server handles SIGINT/SIGTERM and closes its connection pool.

## Current behavior

- One fixed questionnaire, seeded from the existing v2 YAML: interests, strengths, EQ.
- Guest attempts use an encrypted, attempt-scoped session cookie. Google sign-in is optional. An email supplied in a form never grants account access or links to someone else's account.
- A lightweight attempt is created at start. Only completed stages are saved to the server; local browser drafts retain the existing behavior.
- All questions in a stage must be answered. Interests comes first, then strengths, then EQ. The latter two require a single paid unlock for that attempt.
- Finalized answers cannot be edited. Identical retries return the original report ID; conflicting retries return 409. Snapshots preserve question wording, selected labels, and cumulative answers.
- Every stage creates a pending report with no payload or generation timestamp. The UI displays saved-answer placeholder text and keeps checkout/continuation available. No scores, career facts, or prose are generated.
- Sign-in claims only this browser's anonymous attempts and transfers their entitlements. Purchase rows retain the original payer. Logout removes session ownership and clears local drafts/query caches. Like other stateless cookie sessions, logout does not remotely revoke a previously copied cookie; expiry is seven days.
- Seeding unchanged content is repeatable. Changing seeded content after attempts exist is deliberately refused; content/version migration is outside this phase.
- No school/event tables. The legacy optional `school` request field is accepted but discarded.

## Google and payments

Configure `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, and register this exact callback with Google:

```text
<APP_ORIGIN>/api/auth/google/callback
```

OAuth checks state, PKCE, nonce, token signature, issuer, audience, expiry, and verified email. Only the configured origin is used for redirects. Use HTTPS and `NODE_ENV=production` outside local development.

Configure `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID` (an active, one-time price), and `STRIPE_WEBHOOK_SECRET`. `/api/pricing` retrieves the configured Stripe price, so displayed pricing matches Checkout. Register `/api/stripe/webhook` for:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `charge.refunded`

Fulfillment is shared by webhook and checkout-return verification. Transactions and unique Stripe IDs prevent duplicate grants. The actual checkout price/amount/currency are recorded. Current refund state is re-read before fulfillment to handle out-of-order events. Full refunds revoke future paid submissions; partial refunds preserve access. Already submitted answers and stored reports remain readable. Failed/delayed payments grant nothing until confirmed.

Both discount application and checkout requests containing a nonempty discount code return 501. The `discount_codes` table is retained for future use but is never used to grant access. PDF endpoints also return 501; PDF controls and automatic-email promises are removed. There is no email sender in this backend.

## Protected result publication

Generate a separate random `RESULT_API_SECRET` (at least 32 characters) and configure it only on servers. Blank disables publication. It is never a `VITE_` variable or a student credential.

```text
PUT /api/integrations/reports/<report_id>/result
Authorization: Bearer <RESULT_API_SECRET>
Content-Type: application/json
```

The request envelope is:

```json
{
  "format_version": 1,
  "payload": {
    "holland_code": [],
    "traits": {},
    "strengths": [],
    "families": [],
    "careers": [],
    "challenges": []
  }
}
```

This empty example demonstrates the format, not generated guidance. The full validated structure is defined in `src/domain.ts` and follows the existing frontend's report layout. Family identifiers are references within the JSON payload; no career catalog tables are introduced. Do not publish HTML: the frontend renders plain text.

A successful PUT atomically marks that pending report ready and assigns a server generation timestamp. Identical retries succeed; different replacements return 409. Student cookies alone cannot publish. Ownership, input snapshot, stage and report ID cannot be changed through publication. No LLM or public report-generation endpoint is included. Future providers should call the provider-neutral publication service or this endpoint.

Report reads retain existing routes and add `status`, `stage`, `created_at`, nullable `generated_at`, and nullable `payload`. Submission still returns `{ "report_id": "..." }`. The frontend polls pending reports only while active and stops on ready results. `/api/auth/me` includes pending entries in account history.

## Tests and operation

```sh
npm test
npm run typecheck
npm run build
```

Tests start a disposable **real PostgreSQL 18** cluster under the OS temporary directory, apply the checked-in SQL migration, and clean up afterward. They do not read `.env`, use `DATABASE_URL`, reset an existing database, call Google/Stripe, or send emails. Install scripts for the embedded PostgreSQL platform package may require package-manager approval; the test fixture can restore required symlinks automatically. Running tests requires permission to open local sockets and execute the PostgreSQL binaries.

Integration coverage includes cumulative input snapshots, malformed answers, atomic/concurrent retries, ownership isolation, Google claiming, origin checks, duplicate payments, delayed payments, refund ordering, webhook signatures, disabled features, and protected result publication. Google and Stripe behavior is mocked except for local webhook signature verification; live sandbox smoke tests remain a deployment prerequisite with your own credentials.

The worker intentionally has no jobs or queue tables. Add a durable PostgreSQL-backed queue when background generation/delivery is implemented. Do not represent placeholder reports as queued generation: without an external publisher they remain pending indefinitely.

`/health` checks a real database round trip. Request bodies, cookies, bearer tokens, OAuth codes, and report content are not logged. Limits apply per API process; multi-instance deployments should enforce shared request limits at the reverse proxy. Back up the new database, configure production secrets/HTTPS, and deploy the API/frontend together because the pending-report contract differs from the Python backend. No production deployment is performed by this change.
