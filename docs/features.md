# FutureQuest — what exists

A running inventory of built capability, for answering "what can it do right now?" without
reading the code. **Update this whenever a feature lands or a deferral changes.**

Last updated: 2026-10-28 (Stripe checkout)

---

## Client-facing

### Account

- **Sign in with Google**, entirely optional — the questionnaire, report and PDF all work
  without it on the ownership cookie alone
- Signed in, past reports are reachable from any device at `/reports`, with a PDF link each
- The report footer offers sign-in only when signed out, since once signed in the report is
  already reachable from anywhere
- A failed or cancelled sign-in returns to the landing page with an explanation, not a dead end
- **Signing in claims work done in that browser**, whatever email was typed at capture, so a
  report finished anonymously appears in the account and is reachable from any device. Only
  responses whose owner has never signed in are moved, so a shared browser can't transfer one
  person's work to another
- A signed-in visitor is never asked for an email again; their response belongs to their
  account regardless of what any form carries
- `questionnaire_responses.contact_email` records the address results were promised to, so it
  survives the response being re-pointed at a different account

### Capture

- Landing page taking name, email and optional school; malformed email rejected
- A returning visitor is offered "pick up where you left off" or "view your results"
- Starting again with the same email reuses the existing account rather than failing

### Questionnaire

- One question at a time, with back/next navigation
- Progress shown **within the current section** ("Interests · 3 of 14"), so 40 questions reads
  as named stretches rather than one long march
- Four question types: single choice, multi-select, 1–5 Likert scale, free text
- Multi-select caps selection at the limit rather than rejecting the submission afterwards
- Next stays disabled until the current question has a usable answer
- Keyboard: number keys pick an option or scale point, Enter advances
- **Session recovery** — answers persist to `localStorage` as they're given, so closing the tab
  mid-questionnaire loses nothing
- Server validation messages surface verbatim (*"…takes at most 3 options"*) rather than
  collapsing to a generic failure
- **A section is only ever submitted once, and the client knows which are done.** The
  questionnaire reports the sections a response has already had submitted, so a visitor who
  finishes the free tier, pays, and comes back answers only the two they haven't — instead
  of resubmitting the free one and being refused

### Report

- Holland code naming the three leading traits
- A hexagonal radar in canonical RIASEC order, beside labelled bars for all six scores —
  the radar for the shape of a profile, the bars for reading values
- Strengths, and career fields ranked by fit percentage
- Career cards with fit score, rationale, a pay range where one is known, and **per-career next
  steps**: what to study, something to build, something to learn. All optional, silently omitted
  where absent, never invented
- Five to seven "things to watch", drawn from both the strongest and weakest traits
- Paywall teaser naming what the paid sections would add, which disappears when nothing is
  locked rather than offering a section already answered
- **Paying for the full report.** The teaser shows the price the server reports, takes an
  optional discount code, and opens Stripe Checkout. On return, the browser asks the server
  whether the purchase has become access — it doesn't assume, because the webhook that grants
  access may not have landed yet
- **Payment unlocks the questionnaire; it doesn't produce a report.** A report is scored from
  the answers, so the return page sends the visitor into the remaining sections rather than
  waiting for a report that can't exist yet
- **A visitor who has paid but hasn't finished is prompted to finish** — "there's more to
  answer", linking back into the questionnaire — rather than being sold sections they have
  already bought
- **Download as PDF** — server-rendered, same ownership check as the report itself
- **Emailed on completion**, with the PDF attached and a summary in the body. One email per
  completion rather than per section, so the free tier sends one and completing the paid
  sections later sends another. Sent to the address captured at the start, not the account's,
  and only once — `reports.emailed_at` makes it idempotent and is also the retry hook

---

## Server-facing

### Data model

16 tables, with integrity declared rather than enforced in application code:

- Composite foreign keys make answering another questionnaire's question, or selecting another
  question's option, *unrepresentable*
- Functional unique indexes give case-insensitive emails and discount codes
- CHECK constraints enforce per-answer-type shape, entitlement origin and pay ordering

### Content lifecycle

- Publishing and activating are separate decisions, so a version can be staged before going live
- Triggers freeze a published questionnaire entirely — row, questions and options
- `fork_questionnaire()` copies a version into a new draft, with count assertions
- Activation is append-only; rolling back is re-activating the previous version

### Authoring

Four YAML seed loaders — questionnaires, career families, career pay, career guidance — each
validating content before anything is written, so an error names the offending question rather
than a constraint. Families, pay and guidance upsert and are safe to re-run; questionnaires
refuse to overwrite.

Both pay and guidance carry a `source` with a precedence order, so authored or sourced content
outranks placeholder or generated content wherever both exist for a career.

### Scoring and reports

- Deterministic Holland trait scoring, earned-over-available, with stable tie-breaks
- Career families ranked by weighted overlap with the user's scores
- Report payload assembled from templated copy keyed on trait
- `input_hash` over the normalised question/answer text, making the consistency requirement
  measurable
- Provenance recorded per report: `scoring_version`, `model_version`, generation time
- Pay looked up with source precedence (sourced beats placeholder) and omitted on a miss

### PDF export

Server-rendered through WeasyPrint from a print-specific Jinja template — A4, page numbers,
system fonts, cards that don't split across page breaks. Rendering runs off the event loop
since it's CPU-bound, and WeasyPrint is imported lazily to keep it off the startup path.
Missing system libraries surface as a 503 naming them, not a stack trace.

### Payments

- **Stripe Checkout** for a single report. The visitor pays, Stripe redirects back, and the
  entitlement is granted server-side
- **The price comes from the server**, and the Stripe Price is what's actually charged. The
  configured price is checked against the Stripe Price at rollout
- **The webhook is the authority.** The raw body is verified against the signing secret
  before it's parsed; a bad or missing signature writes nothing
- **A purchase grants a `single_report` entitlement** for that one response. The entitlement
  goes to the response's *current* owner, not to whatever the checkout metadata said — a
  response can be re-pointed at a different account between checkout and delivery
- **A completed checkout is idempotent.** The same event delivered three times leaves one
  purchase and one entitlement; the uniqueness is on the database, not on a read-then-write
  check
- **A refund revokes access.** It stamps both `purchases.refunded_at` and the entitlement's
  `revoked_at`, because the gate reads the entitlement
- **Delayed payment methods are handled.** `checkout.session.async_payment_succeeded` grants
  access; `async_payment_failed` grants none. A session that completes before the money
  arrives grants nothing
- **`purchases.paid_at` is when the money arrived**, taken from the Stripe event's timestamp —
  which for a delayed method can be days after the checkout was created
- **The browser's return doesn't race the webhook.** `GET /api/checkout/{session_id}`
  verifies the session with Stripe and, if the webhook is behind, performs the same grant
  inline. A visitor who has just paid is never refused because a background delivery was slow
- **A Stripe session id is not authority to see anything.** The return path checks that the
  caller may access the response the session names
- **Optional.** With no Stripe keys, the app starts and everything else works; the checkout
  route returns 503 naming the missing setting
- **Refunds are issued from the Stripe dashboard.** There is no admin surface; the webhook
  does the revocation

### Platform

- Fourteen public endpoints with generated OpenAPI docs at `/docs`
- Health check that performs a real database round trip
- Three hand-written Alembic migrations
- Async application on asyncpg; migrations on psycopg
- **Multi-origin deployment.** `ALLOWED_ORIGINS` is a list, and CORS, the Google redirect URI
  and the post-sign-in redirect are each chosen per request by matching it against that list.
  One deployment can serve localhost, a tunnel and a staging domain at once; adding another
  means one line in `.env` plus the matching entries in the Google console. Headers only ever
  *select* a configured origin — an unlisted one falls back to the first entry, so a forged
  `X-Forwarded-Host` can't redirect sign-in anywhere. The first entry is also what report
  emails link to, since those are sent with no request to go on.
- **Tunnel-friendly dev server.** `vite.config.ts` allows Cloudflare and ngrok tunnel domains
  by default (`DEV_ALLOWED_HOSTS` overrides), so a demo over a tunnel needs no code change.
  Sign-in over a *quick* tunnel still needs the random hostname added to `ALLOWED_ORIGINS` and
  to the Google console; everything else works without.

### Testing

All against real Postgres, in a throwaway database, each test in a rolled-back transaction.
Includes a model-drift test comparing SQLAlchemy metadata to the migrated schema, and a
structural-equality test that catches a column silently dropped from `fork_questionnaire()`.

The Stripe integration has its own suite (`backend/tests/test_stripe.py`), which covers the
seam rather than the product: the SDK's object shapes, the raw-body signature check,
Stripe's retry semantics, idempotency, refunds and the browser-return path. The outbound
call is faked; the inbound webhook is genuinely exercised, with real event bodies signed by
the real SDK helper, so signature verification is tested rather than stubbed out. It found
four real crash bugs — see `docs/plan.md`, "The Stripe seam, and what it cost".

The frontend has a small suite too (`frontend/src/api/client.test.ts`), covering the payment
calls in the API layer: URLs, request payloads and the error contract. It runs on Node's
built-in test runner with no test framework installed.

---

## Built but dormant

Capability that exists and isn't yet used. Worth knowing before building it twice.

| Thing | State |
| --- | --- |
| Admin accounts with granular permissions | Table exists; only a seed account, for attribution |
| Strengths and EQ sections | Fully answerable and stored, but nothing scores them — the report draws on interests alone |
| Questionnaire forking | Function and tests exist; nothing calls it |
| Report tiers | `free` and `full` modelled; only `free` is ever issued |
| `window` entitlements and Stripe Subscriptions | The gate understands a time-bounded, response-agnostic entitlement, but nothing issues one. Only `single_report` is sold |

---

## Access control

**Per-response ownership.** Starting a response sets a signed, HTTP-only cookie recording that
this browser owns it. Submitting answers and reading a report both check it, and anything
unowned returns 404 — the same status and body as a genuinely missing id, so the response can't
be used to discover which ids exist.

This grants a **capability, not an identity**: it says "you started this", never "you are this
person". That distinction matters because the capture email is never verified — a session
minted from the capture form would let anyone type a known address and inherit that account's
history.

**Google sign-in** supplies the second kind of access. Authlib handles the flow; the callback
checks `email_verified`, then links the Google subject to a user — matching on email first,
since the account usually already exists from the capture form, and on `google_sub` thereafter
because Google emails can change and the subject claim can't. A signed-in user reaches every
response belonging to their account, from any device, with no capability cookie.

Either qualifies. `app/access.py` holds the combined rule, and every protected route uses it.

`/api/auth/me` returns who is signed in and their past reports. Credentials are optional: with
none configured, sign-in returns 503 and everything else works.

**Still missing:** no admin authentication at all — `admin_accounts` and its permission array
exist, but nothing authenticates against them.

## Deliberately absent

Stripe Subscriptions · `window` pricing · Resend email · LLM-generated narrative · admin UI
and admin authentication · a durable job queue

**Stripe Subscriptions** and the **`window` entitlement** are the obvious next step. The
schema supports a time-bounded, response-agnostic entitlement and the gate reads one; nothing
issues them, and nothing charges for one.

**Resend email** is wired but unused — see the caveats.

**The job queue is not built.** Report email still runs on FastAPI `BackgroundTasks`, which
are explicitly not durable: a process restart loses the queued send. The purchase and
entitlement writes are inline and transactional, so nothing on the payment path can be lost
that way.

---

## Known caveats

- **Pay figures are placeholders**, not sourced data. Labelled `mock` in the database and
  "indicative only" in the interface. Replace with BLS/OEWS before anyone makes a decision on
  them.
- **The questionnaire is not a validated instrument.** The content is plausible placeholder
  material, not a psychometrically tested assessment.
- **Entitlements are enforced server-side.** The gate checks `entitlements` table, not a
  deployment flag.
