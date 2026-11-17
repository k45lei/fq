# FutureQuest API

Fastify, TypeScript, Prisma, and PostgreSQL. Follow the [root setup guide](../README.md) before running the API. Application code lives in `src`, schema/migrations in `prisma`, and the fixed instrument in `src/scoring/reference.json`.

## Questionnaire and scoring

The release uses the supplied `temp` reference application's 32 free, 76 strengths, and 30 EQ questions. Stable source keys map questions and options to deterministic UUIDs. Subsections are presentation groups within the three access stages, not separately submitted stages.

- Free assessment: 12 interests, 8 abilities, 6 work style, and 6 values items. Career fit is 70% weighted Holland traits and 30% abilities.
- Strengths: normalized self-direction, achievement drive, structure preference, feedback seeking, motivators, and work-environment signals. Career fit is 40% environment, 40% motivators, and 20% autonomy/structure fit.
- EQ: five domains using reverse-scored agreement items and weighted scenarios. Career profiles specify domain weights and target levels.
- Compass: 40% interest fit, 30% strengths fit, 30% EQ fit. A score spread of at most 15 produces high agreement; at most 30 medium; otherwise mixed. The top five matches are shown, with up to three hidden gems and three conflicting-signal paths.

`reference-138-v1` identifies the instrument; `reference-rules-v1` identifies the rules. Changes to question meaning or scoring require explicit identifier changes and a compatibility plan. No questionnaire-version tables were added. Published answers and report payloads are snapshots, not live views of the current catalogue.

The reference's MD Likert items are collected as context; their answers do not affect motivator scores. Motivators are derived from the reference's forced choices and scenarios. Work-environment smoothing and tie order are preserved. These are reference semantics, not claims of scientific validation. Parity fixtures cover the original scores, archetypes, career rankings, hidden gems, and confidence thresholds.

The catalogue has 56 careers. Unsourced salary, qualification, and outlook claims were removed. The reference bundle is used only to establish fixtures and recover data; runtime code does not load the bundle, invoke browser scripts, or call an LLM.

## API contract

| Route | Behavior |
| --- | --- |
| `GET /health` | Checks PostgreSQL and seeded release question count; returns 503 if not ready |
| `GET /api/questionnaire?response_id=...` | Returns owned attempt snapshot, available stages, completion state, question controls, and subsection metadata |
| `POST /api/responses` | Starts a guest or signed-in attempt with `{name,email,school?}` |
| `POST /api/responses/:id/sections/:stage` | Validates and stores one complete stage and its ready report atomically; returns `{report_id}` |
| `GET /api/reports/:id` | Ownership-protected report, display name, format/ruleset versions, status, and payload |
| `GET /api/responses/:id/report` | Latest owned report |
| `GET /api/auth/me` | Identity and report history, including stage |
| `GET /api/pricing` | Configured Stripe amount and currency |
| `POST /api/responses/:id/checkout` | Creates Stripe checkout for the remaining stages |
| `GET /api/checkout/:sessionId` | Verifies payment, reconciles fulfillment, and returns access state |
| `POST /api/stripe/webhook` | Verifies the signature against raw bytes and reconciles payment/refund events |

Question responses add `reference_key`, `subsection`, `min_selections`, `scale_min_label`, and `scale_max_label`. Source keys identify scoring inputs; callers still submit UUID question/option IDs. Bipolar controls use integers from -2 to 2. Forced choices require exactly three or two selections as specified. The API independently validates every answer, ignores no missing question, and prevents stage skipping.

Format 2 retains the existing report fields and adds headline, summary, scored abilities/values, detailed trait profiles, motivators, work environment, strengths/EQ profiles, detailed career exploration guidance, and a Compass result. Scores in report payloads use 0–1 except bipolar work-environment scores, which use -100–100. The full validated contract is `reportPayloadV2` in `src/domain.ts`; browser types mirror it.

Interests produces eight interest/ability matches. Strengths retains the interest profile and adds its work-style profile and eight strengths matches. EQ retains those profiles and adds EQ and five cumulative Compass matches. Every report retains all answers submitted up to its stage. Reports are calculated in the submission transaction: scoring/validation failure rolls back answers and completion together. Identical retries return the existing report ID; conflicting answers return 409.

## Ownership and payments

Guest access uses an encrypted, HTTP-only cookie containing owned attempt IDs (most recent 20, seven-day expiry). Google sign-in validates the identity and claims only that browser's guest attempts. An unverified form email never establishes account ownership. Logout clears browser drafts and caches; it does not revoke a previously copied stateless cookie remotely.

Payment fulfillment is shared by signed webhooks and checkout-return verification. Transactions and unique Stripe IDs prevent duplicate purchases/entitlements. Refund state is re-read before granting access to handle out-of-order events. Full refunds block future paid submissions; partial refunds do not. Submitted reports remain readable.

School is optional free text stored on each attempt, including signed-in attempts. Surrounding whitespace is trimmed; omitted, null, or blank values are stored as null. School names are limited to 300 characters after trimming. School does not affect identity, access, or scoring. Existing attempts receive null when the migration runs; each new attempt stores its own submitted value.

Discount and PDF endpoints return 501. The discount table is reserved and grants no access. There are no products, orders, refund, job, school, or event tables.

## Protected publication compatibility

`PUT /api/integrations/reports/:reportId/result` accepts `{format_version,payload}` with format 1 or 2. It requires `Authorization: Bearer <RESULT_API_SECRET>`; blank configuration disables it. The secret is server-only and must be at least 32 characters. Student cookies do not authorize publication.

Existing pending reports can be published. Identical retries succeed; different replacements of ready reports return 409, including attempts to replace locally generated results. Publication cannot change ownership, stage, or input snapshots. No outgoing report-provider request is made.

## Operations

`npm run build` emits compiled code and the reference JSON into `dist`. `npm start` runs the compiled API, `npm run db:seed:production` seeds using compiled code, and `npm run worker:start` runs the idle worker. Use `npm run db:migrate` for checked-in migrations before seeding and startup. Schema changes must not rewrite historical migrations.

Local checks: `npm run typecheck`, `npm test`, `npm run build`. Integration tests allocate their own PostgreSQL cluster, apply all migrations, and clean up. They do not load `.env` or contact real providers. Scoring and production-configuration tests run alongside integration tests.

Request logs exclude cookies, tokens, OAuth codes, and report contents. Rate limits apply per API process; use a shared edge limit for multiple replicas. Backups, restore testing, provider credentials, DNS, TLS, and webhook delivery must be verified for the target environment. See [deployment](../deploy/README.md).
