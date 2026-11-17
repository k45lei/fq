# Working on FutureQuest

## Purpose and agreed scope

FutureQuest helps elementary, middle, and high school students explore their interests,
strengths, and future goals. The current phase replaces the backend with Fastify,
TypeScript, Prisma, and PostgreSQL while keeping the React frontend integrated.

Preserve these decisions unless the user explicitly changes them:

- Students can start as guests. Google sign-in is optional. The current user role is
  `student`; keep the model extensible without implementing additional roles now.
- One fixed questionnaire has three ordered stages: interests, strengths, and EQ.
  Interests is free. One payment unlocks both remaining stages for that attempt.
- Save answers only when a complete stage is submitted. The existing lightweight attempt
  created at start and browser-local drafts support continuation; do not add server-side
  draft tracking or a `set_submissions` table.
- Preserve completion timestamps, question/option snapshots, and cumulative answers.
  Identical submission retries return the existing report; conflicting retries fail.
- Generate deterministic reports in the backend on completed-stage submission, using the
  supplied reference instrument: 32 free, 76 strengths, and 30 EQ questions. No LLM is used.
  The protected publication API remains for compatibility with earlier pending reports.
- Keep purchases, entitlements, and the reserved discount-code table. Do not introduce
  products, orders, or separate refund tables. Discounts currently return an explicit
  unsupported-feature error.
- Keep a separate worker entrypoint, intentionally idle until background work is needed.
- Schools, events, event participations, participant access, questionnaire versioning,
  PDF generation, email delivery, and job queues are outside this phase.
  Optional school text on an attempt is supported; school entities and management remain out of scope.
- The new database is a fresh installation. Migrating legacy application data is outside
  the agreed scope.

## Read the right sources

Current user instructions take precedence over this file. Distinguish intended behavior
from the current implementation; a code discrepancy is not permission to change the scope.

| Source | Use |
| --- | --- |
| [Root README](README.md) | Local setup, current stack, known setup blockers |
| [Backend guide](backend/README.md) | API behavior, payment events, result-publication contract, test setup |
| [Prisma schema](backend/prisma/schema.prisma) and [migrations](backend/prisma/migrations) | Implemented database structure and constraints |
| [Backend integration tests](backend/test/integration.test.ts) | Executable coverage of ownership, submissions, payments, and publication |
| [Frontend API types](frontend/src/api/types.ts) and [client](frontend/src/api/client.ts) | Browser/API contract |
| [Design conventions](docs/design.md) | Existing typography, tokens, components, and interaction patterns |

[Requirements](docs/requirements.md), [schema draft](docs/schema.md),
[feature inventory](docs/features.md), and [payment plan](docs/plan.md) contain historical
designs. They are background, not the current implementation checklist. Do not recreate
their Python backend, expanded tables, or PDF features. The design document also has
outdated audience, PDF, and testing descriptions. Current scoring follows the supplied
reference bundle, ported into typed modules in `backend/src/scoring`.

Read the relevant source and tests before editing. Update affected documentation when
behavior or setup changes; do not copy historical claims into new instructions.

## Readability conventions

Use the expanded style established in commit `7a0d2eb` (`chore: make code look prettier`).
[auth.ts](backend/src/auth.ts) and [questionnaire.ts](backend/src/questionnaire.ts)
are useful examples.

- Use two-space indentation, semicolons, and trailing commas in multiline structures.
  Keep backend single quotes and frontend double quotes; avoid unrelated quote churn.
- Use explicit braces for conditionals and loops, including early returns and `continue`.
  Put each statement and variable declaration on its own line.
- Separate logical steps with blank lines: prepare input, validate, perform work, return.
  Keep closely related declarations together rather than inserting a blank after every line.
- Expand substantial objects, nested database operations, types, and test fixtures so their
  fields can be scanned. Small leaf objects such as `{ id }` can stay inline.
- Wrap long calls and conditions. Avoid nested ternaries and compressed callbacks when
  they obscure control flow. Long literal URLs or Tailwind class strings may stay intact.
- Prefer `const` arrow functions for backend functions and new standalone helpers. Preserve
  existing frontend component/hook declarations where conversion would only add churn.
- Use descriptive names and small functions with a clear purpose. Keep comments focused
  on constraints and reasons; remove stale explanations when touching the related logic.
- Keep style-only changes behavior-preserving. Do not mix formatting with payment,
  authorization, schema, or product changes without making those changes explicit.
- Do not hand-format generated Prisma code, dependencies, build output, or lockfiles.

## Architecture and invariants

- Backend application code lives in `backend/src`; schema changes need a checked-in
  Prisma migration. Keep HTTP handling, questionnaire rules, payments, and publication in
  their existing modules rather than adding layers without a concrete need.
- Validate untrusted input at the API boundary. Enforce ownership, stage ordering, and
  payment access on the server, regardless of what the frontend displays.
- Never use an unverified form email as proof of account ownership. Preserve guest access
  and the verified Google identity/attempt-claiming flow.
- Keep finalized answers immutable and preserve transactional retry handling. Each report
  must retain the cumulative input snapshot for its stage.
- Keep payment fulfillment shared between webhook and checkout-return verification.
  Verify webhook signatures against raw request bytes; preserve duplicate-event handling
  and out-of-order refund reconciliation. Full refunds revoke future paid submissions;
  partial refunds retain access. Stored answers and reports remain readable.
- Result publication requires server credentials and validated payloads. Student cookies
  cannot publish results. Identical publication retries succeed; replacements conflict.
- Keep API client code and TanStack Query hooks responsible for frontend server state.
  Zustand holds questionnaire drafts. Preserve logout cleanup and checkout continuation.
- Never commit `.env`, credentials, dependencies, or generated output. Use `.env.example`
  for configuration names and safe examples.

## Release goals and acceptance criteria

The user approved deterministic reports and the complete reference instrument on 2026-11-14,
superseding the earlier placeholder phase. The target is ready to deploy, not an automatic
hosting deployment. No new database tables are required.

- Seed the fixed 138-question instrument from checked-in data, safely refusing incompatible
  reseeding where attempts exist. Preserve the user's databases and local Compose volume.
- Validate answers and calculate ready cumulative reports in the same transaction. Preserve
  reference scoring, choice constraints, reverse scoring, stable tie order, and immutable retries.
- Keep scoring/career data in versioned backend files. Record instrument/ruleset identifiers
  in snapshots and reports. Omit unsourced salary, outlook, and qualification claims.
- Integrate the example frontend design with real APIs, guest access, paid continuation,
  sign-in, account history, ready reports, and the final Compass. Preserve reference HTML/CSS.
  Match the five mockups through scoped styles in `frontend/src/mockups.css`. Selection
  requires Next; completing a stage requires Submit answers. Do not auto-advance.
- Pass backend and frontend checks and exercise the browser journey. Distinguish mocked
  Google/Stripe tests from actual configured-provider verification.
- Keep production instructions current: HTTPS/static hosting, required secrets, database
  backups/restore, migrations, and provider callbacks. Record unverified deployment checks.

See [deployment instructions](deploy/README.md) and [verification record](docs/verification.md)
for the current evidence and external release gates. Do not claim live readiness solely from
mocked tests. Email, sharing, PDF, discounts, school/event management, and active worker jobs remain out
of scope. The worker remains idle because deterministic scoring runs synchronously.

## Commands and verification

There is no root Node package. Use npm in `backend` and pnpm in `frontend`.

| Directory | Commands |
| --- | --- |
| `backend` | `npm ci`, `npm run db:generate`, `npm run typecheck`, `npm test`, `npm run build` |
| `backend` | `docker compose up -d --wait`, `npm run db:migrate`, `npm run db:seed`, `npm run dev` |
| `frontend` | `pnpm install`, `pnpm typecheck`, `pnpm test`, `pnpm build`, `pnpm dev` |

Use `backend/compose.yml` or the root Compose file for the same local database on port 5433.
Both retain the original `backend-ts` project/volume identity; do not delete volumes. `APP_ORIGIN` must match the browser-facing frontend origin; Vite proxies
`/api` to port 8000 by default. Follow the root README for first-time environment setup.

Backend integration tests start disposable PostgreSQL and require local sockets. They
must not reset the application database or call live providers. Run checks appropriate
to the change; use existing tests for formatting work rather than adding tests that only
mirror syntax. Review `git diff --check` and the final diff. Report passes, failures, and
checks that could not run separately.

## Working with the user

Proceed with implementation already authorized in the conversation. For analysis-only
requests, inspect and propose changes without writing code. Ask concise questions when a
missing business decision or credential/configuration detail actually blocks the task;
do not repeatedly ask for permission already granted. Continue independent work while
clarification is pending. Preserve the user's edits and keep scope changes explicit.
