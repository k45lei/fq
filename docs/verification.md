# Release verification

Last updated: 2026-11-16. This record covers the deterministic-report release in the working
tree. It does not establish that a hosting environment or provider account is configured.

## Automated checks

| Check | Result |
| --- | --- |
| Backend `npm run typecheck` | Passed |
| Backend `npm test` | 29 passed |
| Backend `npm run build` | Passed |
| Frontend `pnpm typecheck` | Passed |
| Frontend `pnpm test` | 24 passed |
| Frontend `pnpm build` | Passed |
| Local Compose configuration, root and backend | Passed |
| Production Compose configuration with example variables | Passed |
| Chromium journey with mocked Google and Stripe | Passed, all 138 questions and three reports |
| Application diff whitespace checks | Passed; supplied reference bundles excluded |

Backend integration tests create disposable PostgreSQL, apply both checked-in migrations,
and seed all 138 questions. They cover unchanged reseeding, cumulative report snapshots,
ownership, stage ordering, immutable submissions, concurrent retries, rollback on scoring
failure, exact forced-choice counts, bipolar limits, and protected publication. Payment
tests cover fulfillment, redelivery, refund ordering, full/partial refunds, and raw-body
signature verification. Production tests cover required configuration and per-visitor
rate limiting through the single trusted proxy.

Frontend regression tests also verify that returning from checkout preserves drafts for
the same attempt and clears drafts belonging to another attempt.

Twelve scoring fixtures compare the port with captured output from the supplied reference
implementation, including profiles, rankings, archetypes, and Compass results. The source
instrument has 32 interests, 76 strengths, and 30 EQ questions and 56 careers. No LLM runs.

## Browser check

The repeatable browser check is [scripts/browser-check.cjs](../scripts/browser-check.cjs).
Its [API fixture](../backend/test/browser/server.ts) creates a separate disposable database
and binds only to loopback. It never loads the application's `.env`. Google identity and
Stripe checkout are mocked; actual session, API, database, scoring, and React code run.

The journey exercises desktop/mobile layout, theme persistence, guest start, draft recovery
after refresh, all 138 question controls, three ready reports, checkout cancellation and
return, paid continuation, Compass, Google guest-claiming, account history, and logout.
It also checks for browser exceptions and horizontal overflow. This journey passed on
2026-11-16. The subsequent cross-attempt draft fix passed the frontend regression suite;
the complete browser journey was not repeated for that isolated change. Screenshots are
saved outside the repository by default.

Run the frontend production build first. With dependencies installed, start these in
separate terminals:

```sh
cd backend
node --import tsx test/browser/server.ts
```

```sh
cd frontend
API_PROXY_TARGET=http://127.0.0.1:8001 pnpm exec vite preview --host 127.0.0.1 --port 4175 --strictPort
```

Then run from the repository root, with Chromium installed:

```sh
npm install --prefix /tmp/futurequest-tools --no-audit --no-fund playwright-core
PLAYWRIGHT_MODULE=/tmp/futurequest-tools/node_modules/playwright-core node scripts/browser-check.cjs
```

`CHROMIUM_PATH` overrides `/usr/bin/chromium`. If the test environment cannot run Chromium's
sandbox, `CHROMIUM_NO_SANDBOX=1` is available for this isolated test. `BROWSER_SCREENSHOTS`
overrides `/tmp/futurequest-browser-screenshots`. Stop both servers after the check; the API
fixture removes its temporary database when it receives SIGINT or SIGTERM. Restart the
fixture before repeating the journey so account history starts empty. These test routes
are not registered by the application server.

## Remaining release gates

- **Container build/runtime:** configuration validates, but the Docker daemon is unavailable
  in this environment. Neither production image nor the complete HTTPS Compose deployment
  has been verified here.
- **Google:** complete login and guest claiming with the target OAuth client and callback.
  Local mocked identity verification does not verify Google's configuration.
- **Stripe:** verify the configured price, test-mode checkout, actual webhook delivery and
  redelivery, cancellation, and partial/full refunds. Local payment tests do not verify the
  configured Stripe account or webhook endpoint.
- **Hosting:** configure DNS, HTTPS, production secrets, database TLS/backups, monitoring,
  and a restore exercise. No production deployment was performed.

Follow [the deployment guide](../deploy/README.md) for these checks. Use a fresh database
for this instrument; existing application databases and Docker volumes were not reset.

## Content limits

The reference's MD Likert answers are retained as context but do not affect motivator scores;
the reference uses forced choices and scenarios for those scores. Confidence means agreement
between component scores, not a probability of career success. Unsourced salary, outlook,
and qualification claims are omitted. PDF, email, sharing, discounts, school/event features,
and active worker jobs remain outside this release.
