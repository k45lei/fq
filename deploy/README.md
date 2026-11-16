# Deploying FutureQuest

The supplied `compose.production.yml` runs the compiled API and a Caddy HTTPS/static frontend. PostgreSQL is supplied separately, preferably a managed service with backups. No deployment is performed automatically.

## Prepare

- Provision a dedicated empty PostgreSQL database and a hostname pointing at the host.
- Allow inbound 80/443 for Caddy certificate issuance and HTTPS. Do not expose API port 8000 publicly.
- Create a private environment file outside Git (for example `backend/.env.production`). Set `PUBLIC_HOST` to the bare hostname, `DATABASE_URL` to the production database, and a unique random 64-hex-character `SESSION_KEY`.
- Set Google and Stripe variables described in the root README. `APP_ORIGIN` is set by Compose to `https://${PUBLIC_HOST}`. Use Stripe test mode for initial verification; configure a live price and its matching live webhook secret only when launching paid access.
- Register Google's exact HTTPS callback and the four Stripe event types. A development tunnel, Vite proxy, or test API fixture is not a production service.

Use TLS for a remote database connection with the provider's required certificate verification settings. Do not use the local Compose password in production.

## Build, migrate, seed, start

Run from the repository root, substituting your private environment-file path:

```sh
docker compose --env-file backend/.env.production -f compose.production.yml build
docker compose --env-file backend/.env.production -f compose.production.yml run --rm api npm run db:migrate
docker compose --env-file backend/.env.production -f compose.production.yml run --rm api npm run db:seed:production
docker compose --env-file backend/.env.production -f compose.production.yml up -d --wait
```

Database migrations and seeding are separate release operations. Do not run destructive resets or reseed over a different instrument with existing attempts. Keep the database URL and session key stable across restarts. Caddy's certificate volumes must persist. `GET /health` checks both connectivity and the seeded questionnaire.

The Docker runtime includes Prisma's migration tooling. The API runs as the non-root Node user. The worker is unnecessary for this release because deterministic scoring is synchronous; the separate entrypoint remains available for future background work.

Fastify trusts one proxy hop in production so rate limiting uses the visitor's address. Keep the API reachable only through Caddy. Review this setting before changing the proxy topology.

## Verify before launch

1. Check `/health` and application logs. Load both `/` and a direct client route over HTTPS.
2. Complete a guest free assessment, refresh its report, and confirm the questionnaire can resume a browser-local draft.
3. Sign in with the configured Google account and verify guest reports are claimed and visible in history.
4. Make a Stripe test-mode purchase. Verify cancellation, return verification, independently delivered webhooks, repeated webhook delivery, both paid stages, and the Compass report.
5. Issue partial and full test refunds and confirm access behavior. Saved reports must remain readable.
6. Verify the domain, live/test separation, backups, restore procedure, and monitoring before enabling real payments.

Local automated tests use mocked providers. Until steps 3–5 succeed with the configured provider accounts, provider readiness is unverified. The source reference's career claims are not a validated assessment; unsourced salary/outlook/qualification details are intentionally absent.

## Backups and rollback

Use provider-managed point-in-time recovery plus scheduled encrypted backups, or schedule `pg_dump` in custom format to protected storage outside the application host. Retain backups according to an agreed retention policy and regularly restore to a separate database using `pg_restore --no-owner`. Never test restoration against the running database.

Before a release, take a recoverable backup and record the application image revision and migration state. Use forward-compatible application rollback where possible; do not automatically reverse database migrations or delete volumes. A database restore requires a maintenance window and a decision about writes made since the backup.

Monitor external HTTPS health, 5xx rates, database capacity, failed Stripe deliveries, and backup freshness. Alert on unhealthy containers and payment-webhook failures. Avoid logging student answers, report payloads, session cookies, or credentials.
