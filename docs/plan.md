# Plan — payments and the entitlement gate

Wiring Stripe behind the entitlement gate.

**Estimate: about five days** for the per-report model, plus roughly a day if the window
model is built at the same time. The schema is already done, which is usually the expensive
part.

**Status: Phase 1 and Phase 2 are done and committed. Phase 3 — the frontend — is done in
the working tree.** What follows is the design as it was built, kept because the reasoning
is still worth more than a summary of the diff. The "Blocking questions" section records the
decisions that were actually taken.

---

## Phases

The work is split into three phases so each one ends in something demonstrable rather than in
a half-built integration:

| Phase | What lands | Ends with |
| --- | --- | --- |
| **1** | The gate, the bypass removed, Stripe-free | A paywall that actually holds, and no way around it |
| **2** | Stripe: checkout, webhook, refunds, the money side | A real purchase that grants real access |
| **3** | The frontend: paywall CTA, checkout, return | A visitor who can pay without leaving the app |

Phase 1 is committed as `2f40a73 Enforce the entitlement gate; remove the demo paywall
bypass`. Phase 2 and Phase 3 are in the working tree together, because splitting the
frontend from the backend it consumes would mean a commit whose frontend called an API that
didn't exist.

---

## Where the work actually stands

Phase 1 is **done and committed**. Phase 2 is **done** — the tree passes its tests, its linter
and its type checker. Phase 3 is **done**.

### Phase 1 — done, committed as `2f40a73`

Implemented:

- `app/entitlements.py` — the gate query, in two functions: `has_active_entitlement(session,
  user_id, response_id)` and `has_any_active_entitlement(session, user_id)`. One shared query
  builder, so the two can't drift.
- `app/routes/public.py` — `preview_sections()` (free only), `sections_for()` and
  `sections_for_any_response()`, replacing the old settings-driven `available_sections()`.
- The demo bypass is gone: the setting, the startup warning, the `unlocked` fixture, its uses
  in tests, and the entries in `.env.example`, `README.md`, `docs/features.md` and `CLAUDE.md`.
- `tests/test_entitlements.py` — thirteen tests covering the gate's refusals as well as its
  grants: revoked, expired, not-yet-started, someone else's, another response's, and the
  `coaching_session` kind that must not unlock a report.

**What the four failures were, and what they turned out to be.** They were the price of
writing tests against a gate that didn't exist yet, and each one is now a test that protects
something real:

1. **Two tests faked a sign-in.** They set `client.cookies["fq_session"] = str(user.id)`, but
   `fq_session` is an `itsdangerous` `TimestampSigner` cookie, not a bare UUID — so
   `current_user_id()` returned `None` and the gate never saw the entitlement. The helper that
   did it properly is now shared, in `tests/factories.py` as `sign_in_directly(client, user_id)`,
   and used by `test_api.py`, `test_auth.py` and `test_entitlements.py` alike. **A test that
   reaches past the session format to fake an identity is testing nothing**; it passed only
   because the gate wasn't there yet.

2. **`test_answers_for_another_section_are_still_refused` was testing a rule that no longer
   existed.** It submitted *every* question's answers against the `interests` section and
   expected the "outside this section" rejection — but the free tier only serves interests
   questions, so the submission was correct and the test's premise had gone. It now asserts
   the cardinality rule that still exists: a `multi_choice` question takes at most
   `max_selections` options, and the server checks it.

3. **`test_an_unfinished_questionnaire_schedules_nothing` was asserting bypass-era
   behaviour.** Its docstring said "With all three sections available, finishing only the
   first isn't finishing" — true under the bypass, false now. Under the gate the free tier
   *is* finished when interests is done, so one email is correct. The test now asserts the
   behaviour that matters: **a partial section schedules nothing, the free tier schedules
   one email, and the paid sections schedule a second.** That's the `has_completed(response,
   sections)` rule in `app/responses.py`.

**The cleanups that went with them.** `ruff check` had 28 errors and `ruff format --check`
wanted four files reformatted; both are clean now. `frontend/src/api/types.ts` was
syntactically broken (a stray line above the docstring) and no longer is. The tracked
`frontend/tsconfig.tsbuildinfo` — a build cache, not source — is untracked and ignored. The
`tests/fixtures/` package and the duplicate `admin_account` fixture are gone.

**One thing deliberately left alone.** The Stripe draft that was in `app/routes/public.py` has
been removed from the working tree rather than repaired in place. It could not work (see
"What does not exist" below), and half-repaired payment code is worse than none: it would
have looked finished. A copy of it is preserved at `/tmp/fq-drafts/stripe-draft.patch` for
reference, and the analysis of what's wrong with it is kept below, because each mistake is
worth not repeating.

### Phase 2 — done

Built, in the order the plan suggested:

- **`app/stripe_client.py`** — the SDK's owner. `client()`, `price_id()`, `report_price()`,
  `webhook_secret()`, `checkout_metadata()`, and `PaymentsNotConfiguredError`. The client is
  `stripe.StripeClient(...).v1...`, not the deprecated module-level or `client.checkout` paths.
- **`app/routes/payments.py`** — `GET /api/pricing`, `POST /api/responses/{id}/checkout`,
  `POST /api/stripe/webhook`, `GET /api/checkout/{session_id}`.
- **`app/payments.py`** — `grant_purchase()`, `revoke_for_refund()`, and the small helpers
  (`parse_uuid`, `metadata_value`) that keep the SDK's typed objects from being treated as
  dicts.
- **`app/schemas.py`** — `CheckoutRequest`, `CheckoutOut`, `PricingOut`, `CheckoutStatusOut`.
- **`app/config.py`** — `stripe_price_id`, `report_price_cents`, `report_price_currency`, and
  the two readiness flags.
- **`tests/test_payments.py`** — thirty-five tests, with the outbound Stripe calls faked and
  the inbound ones exercised with real SDK-generated signatures.
- **`tests/test_stripe.py`** — a hundred and six tests covering the *integration seam*
  rather than the product consequences: the SDK's object shapes, the raw-body signature
  check, Stripe's retry semantics, idempotency, refunds, and the return path. See
  "The Stripe seam, and what it cost" below.

**Four things the plan called for that were not obvious.**

1. **The questionnaire preview had to become response-aware.** `GET /api/questionnaire` takes
   an optional `?response_id=`. Without it the route asks "what may this visitor see before
   there's a response to ask about", and the only entitlement that can widen that is a
   `window` one — a `single_report` names a response, and there's no response yet. So a
   visitor who had just paid for a specific response was still served only the free tier.
   With the id, the route checks access to the response first and asks the gate about *its
   owner* — the same rule the submit route uses.

2. **The report tier had to come from the same gate answer as the sections.** The submit route
   asks the gate **once** and derives both from it. Two queries could disagree — a second one a
   moment later might see an entitlement that has just been revoked — and then the visitor
   would be allowed to answer a paid section and be handed a report that says it wasn't paid
   for. `submit_section()` takes a `tier` argument, and the route passes it.

3. **`paid_at` is the event's timestamp, not `checkout.created`.** For a delayed payment
   method those differ by days, and `purchases.paid_at` is the record of when the money
   arrived. The entitlement's `valid_from` uses the same time.

4. **Delayed payments needed explicit handling.** `checkout.session.async_payment_succeeded`
   grants access; `checkout.session.async_payment_failed` grants none and is acknowledged.
   An initial `checkout.session.completed` for an unpaid session grants nothing.

### Phase 3 — done

- **`src/api/types.ts`** — `Pricing`, `Checkout`, `CheckoutStatus`.
- **`src/api/client.ts`** — `pricing()`, `createCheckout()`, `checkoutStatus()`, and
  `latestReport()`.
- **`src/api/queries.ts`** — `usePricing()`, `useCreateCheckout()`, `useCheckoutStatus()`,
  `useLatestReport()`. The status query polls only while the answer is still "not yet".
- **`src/components/report/PaywallTeaser.tsx`** — the CTA, the discount code field and the
  price, all from the server.
- **`src/pages/CheckoutSuccess.tsx`** and **`src/pages/CheckoutCancelled.tsx`**, routed in
  `App.tsx` at the paths the server builds into the Stripe URLs.

The success page asks `GET /api/checkout/{session_id}` rather than assuming, because the
webhook that grants access may not have landed. The cancel page says plainly that nothing was
charged.

**One endpoint was added for the frontend's benefit:** `GET /api/responses/{id}/report`,
returning the newest report for a response. Reports are append-only, so "the report" is
ambiguous once a purchase can create a second one — and a visitor who has just paid has a
Stripe session id rather than a report id.

**One field was added to an existing one:** `QuestionnaireOut.completed_sections`. See
"Post-payment continuation" below — it is what makes the paid flow work at all.

**No change was needed to the report's own routing.** The submit response returns the report
id, and the questionnaire navigates to it, so the newest report is the one shown.

### Post-payment continuation

*Found and fixed while testing the flow end to end. It is a real bug, not a nicety, and the
reason is worth recording because the fix is not where you'd first look.*

**Payment unlocks the questionnaire; it does not produce a report.** A report is scored from
the answers, so a full report cannot exist until the paid sections have been answered. The
original success page waited for a full report to appear and navigated to it — which would
never have happened, because nothing in the payment path answers a question. The visitor
would have sat on a "thank you" page that resolved to nothing.

So the success page now does the obvious thing instead: once the server confirms the
entitlement, it sends the visitor **into** the questionnaire, and the report appears when
they finish.

**The second bug is the one that would have bitten first.** A section can only be submitted
once, and the server refuses a second attempt with a 422. A visitor who finished the free
section, paid, and came back would have had the client loop over every available section —
including `interests`, which they had already submitted — and the flow would have died on
the first request with a 422.

Two changes fix that:

- **`QuestionnaireOut.completed_sections`** — the server now reports which sections a
  response has already had submitted, using the completion timestamps already on the row
  (`interests_completed_at`, `strengths_completed_at`, `eq_completed_at`). The client skips
  them. The information already existed in the database; it just wasn't in the contract.
- **The questionnaire filters the done sections out** of both the question list and the
  submit loop, and only submits what remains.

**The third issue was a stale cache.** `useQuestionnaire` had `staleTime: Infinity`, on the
reasoning that published questionnaires never change. But the *sections* on offer depend on
the entitlement, and the *completed* list depends on what has been submitted — both change
over a response's life. It now refetches, and `useSubmitSection` invalidates the
questionnaire so the completed list stays accurate.

**And the `reportId` guard.** The persisted store kept the free report's id, and the
questionnaire redirected to it on the way in. For a visitor who has just paid, that report
is the wrong one — the paid sections are still to do. The guard is now on *whether anything
remains to answer*, not on whether a report id happens to be stored.

**Which left a positional bug.** The store held `currentIndex` — a *position* in the
question list. Once completed sections drop out of that list, an index is a position in the
wrong list: a visitor who had reached question 14 of the free section would resume at
question 14 of the paid sections, mid-way, with no idea why. The store now holds
`currentQuestionId` instead. A question id survives the filtering, so the visitor resumes
on the question they were actually on.

**The report page mirrors this**: a visitor who has paid but hasn't finished sees "There's
more to answer" and a link back into the questionnaire, rather than a paywall for sections
they have already bought.

---

## The Stripe seam, and what it cost

*Written after the Stripe test suite. The money path was already working by the time these
tests were written, so the bugs they found were not "the feature doesn't work" — they were
"the feature works, until Stripe sends an object shaped slightly differently than the one
we tested with". That is the class of bug this suite exists to catch.*

`tests/test_payments.py` answers "does a payment do the right thing?". `tests/test_stripe.py`
answers "does the seam survive what Stripe actually sends?" — a hundred and six tests across
the client, the webhook, the return path, refunds and idempotency.

**The outbound call is faked; the inbound webhook is genuinely exercised.** Real event
bodies, signed with `stripe.WebhookSignature.generate_signature_header`, so signature
verification is *tested* rather than stubbed out. The only thing faked is the network call
in the other direction, in the same shape the project already uses for email.

### The SDK's objects are not dicts, and a missing key is worse than a wrong one

This is the bug class that cost the most, and none of it was visible from the happy path.

`checkout.metadata` is a `StripeObject`. It supports `[...]` and raises `AttributeError` on
`.get(...)` — the error message helpfully says so. But the deeper problem is a *missing*
key: the SDK raises `AttributeError` for the attribute itself, so `checkout.metadata`
explodes rather than returning `None`. A session created by hand in the Stripe dashboard
has no `metadata` at all, and it arrives as a real webhook event. The old code did
`checkout.metadata.get("response_id")`, which failed on two counts at once.

The same shape appeared three more times, each in a path that only runs for an unusual
object:

- **`charge.payment_intent`** — absent on a charge that isn't tied to a payment intent.
  A refund event for such a charge crashed the handler.
- **`event.type`** — a body signed with the right secret but carrying no `type` is not an
  event at all. It was being read as if it were.
- **`event.data.object`** — a signed body with no `data` crashed before it could be
  rejected.

All four were **HTTP 500s**: an unhandled `AttributeError` in a webhook handler. Stripe
retries a 5xx for up to three days, so each of these was a repeating failure rather than a
single one. The fix is `stripe_field()` in `app/payments.py`, which reads an attribute if
it's there and returns `None` if it isn't.

**The distinction that matters:** a *missing* field is a malformed event and gets a 400; an
event of an *unhandled type* is well-formed and gets a 200 with the event ignored. Both
stop Stripe retrying, but they mean different things — the first is a broken sender, the
second is a sender telling us about something we don't care about yet.

### The signature is checked against the raw bytes, in that order

The body is read as `await request.body()` and verified before it is parsed. Two tests pin
this down:

- **The raw body is what is verified** — a body that is signed, then re-serialised, is
  refused. Re-serialising the parsed JSON changes the bytes, so the signature no longer
  matches. Parsing first and signing the parsed form would have "worked" in a test and
  failed against Stripe.
- **A forged body is refused before it is parsed.** The order is deliberate: the signature
  says the body came from Stripe, and only then is it worth parsing it. A garbage body with
  a garbage signature gets "invalid signature", not "malformed event" — which also keeps
  the two failure modes distinguishable in the logs.

### The status codes are load-bearing, because Stripe retries

Every non-2xx is retried for up to three days. So:

- **400** for a malformed body, a missing signature, an invalid signature, or a body that
  isn't an event. Nothing to retry.
- **503** when the webhook secret isn't configured. A 5xx here asks Stripe to retry, which
  is right: the delivery will succeed once the secret is set.
- **200** for a handled event, *and* for a valid event of an unhandled type. The latter is
  acknowledged and ignored, so Stripe doesn't keep delivering it.

### Idempotency comes from the schema, not from a check

`INSERT ... ON CONFLICT (stripe_checkout_session_id) DO NOTHING`. A pre-check — "does this
session already have a purchase?" followed by an insert — is a race: two deliveries of the
same event can both read "no" and both insert. The uniqueness constraint is the thing that
makes it correct, and the `DO NOTHING` is what makes the loser a no-op rather than an
error.

The tests fire the *same* event twice, and fire *different* events for the same session —
`checkout.session.completed` and `checkout.session.async_payment_succeeded` for one session
— and assert one purchase, one entitlement, one grant.

### A refund has to revoke the entitlement, not just mark the purchase

`purchases.refunded_at` alone revokes nothing, because the gate reads `entitlements`. A
refund stamps `revoked_at` on the entitlement the purchase issued. A test buys a response,
refunds it, and then checks the paid sections are closed again — the thing a buyer would
actually notice.

Redelivery is harmless: a second refund event revokes nothing more, because the
entitlement is already revoked.

### The return path is a second copy of the grant, and it has to agree

`GET /api/checkout/{session_id}` performs the same grant the webhook would have, using the
same `grant_purchase()`. That's the fix for a visitor who has paid and comes back before
the webhook landed.

Because it's the same code path, the tests check the same properties on it: it grants once,
it records what Stripe charged, it records when the money arrived, and it records the
amount. A `PaymentNotReadyError` there is caught and reported as "paid but not entitled" —
an honest state the browser can poll — rather than being allowed to become a 500.

**A Stripe session id is not authority.** The return path checks that the caller may access
the response the session names, using the same `may_access_response()` the rest of the
application uses. A stranger with a session id gets a 404.

### The frontend's half of the seam

The frontend's rule is that nothing outside `src/api/` may call `fetch`, or know a URL or
a status code. That rule is worth little if the layer itself is untested, because the
mistakes there are in the URLs and the payloads.

`frontend/src/api/client.test.ts` covers the payment calls: that `createCheckout` posts to
`/api/responses/{id}/checkout`, that the discount code is sent as `discount_code` (the
request schema forbids unknown fields, so the wrong name is a 422 rather than a silently
ignored code), that `checkoutStatus` escapes the session id — it comes from the URL bar, so
it can't be pasted into a path unescaped — and that the error contract turns a FastAPI
`detail` string, a field-error list, a non-JSON body and an unreachable server into the
right messages.

These run on Node's built-in test runner, with `rolldown` transpiling the TypeScript
first. No test framework was added: the assertions are about URL construction and payloads,
and Node's runner covers those without a dependency. `frontend/scripts/test.sh` does the
whole thing.

---

## Phase 2 — Stripe

*This section is the design as it was written before the work. What was actually built is
recorded above; where the two differ, the difference is noted inline.*

### What exists

The schema, the models and the config stubs are all in place:

- **`purchases`, `entitlements`, `discount_codes`** — from migration 0001, with the CHECK
  constraints that make the design hold: `entitlements_origin` (bought *xor* comped),
  `entitlements_shape` (a `single_report` must name a response; a `window` must not, and must
  have an expiry), `entitlements_validity`.
- **ORM models** in `app/models/payment.py`, matching those tables.
- **`stripe_secret_key` and `stripe_webhook_secret`** in `app/config.py`, both optional.
- **The `stripe` package** — 15.6.1, already in `uv.lock` and installed.

**No migration is needed.** Every column Phase 2 writes already exists. The one optional
addition — a `stripe_events` table for dedupe across event *types* — is deferred; see step 3.

### What did not exist

*Written before the work. For reference: the Stripe client, the routes, the tests and the
discount handling are all built now. The analysis of the removed draft below is the part
still worth reading.*

- **A Stripe client.** There is no module that owns the SDK. Nothing in `app/` imports
  `stripe` at all.
- **Any route that takes money.** There is no checkout, no webhook, no refund handler. The
  frontend's paywall button is inert and says so.
- **Any test that exercises the money path.** `tests/test_entitlements.py` covers the gate
  query only.

### The draft that was there, and what was wrong with it

There was a partial Stripe implementation in `app/routes/public.py` (uncommitted). It has
been removed rather than repaired in place, and a copy is preserved at
`/tmp/fq-drafts/stripe-draft.patch`. The reasons are worth keeping, because each one is a
place where the obvious implementation is subtly wrong.

**It cannot run at all.** The webhook's `checkout.session.completed` handler constructs
`Purchase(amount=2999, ...)` and `Entitlement(origin="paid", ...)`. Neither column exists: the
column is `amount_cents`, and origin is expressed by the `purchase_id`/`granted_by` pair being
exactly one of the two (enforced by `entitlements_origin`). Verified against the model — both
raise `TypeError: 'amount' is an invalid keyword argument`. And
`Entitlement.stripe_payment_intent_id` doesn't exist either, so the refund handler's
entitlement lookup is a column that isn't there.

**It would be rejected by Stripe on a real checkout.** `stripe.checkout.Session.create(discount_codes=[...])`
is not a parameter of the Stripe API — the parameter is `discounts=[{"coupon": ...}]`. The SDK's
typed params confirm it: `stripe/params/checkout/_session_create_params.py` defines
`SessionCreateParamsDiscount` with exactly two fields, `coupon` and `promotion_code`, and
`SessionCreateParams` has `allow_promotion_codes` and `discounts` — and no `discount_codes`
anywhere. The SDK passes unknown parameters straight through to the API, so the call reaches
Stripe and comes back as a 400 ("Received unknown parameter: discount_codes"), not as a
`TypeError` — which is why this one hides so well in local testing.

**The webhook handlers would crash on the event object itself.** `construct_event` returns
`stripe.checkout._session.Session` and `stripe._charge.Charge` objects, not dicts — and those
classes deliberately have no `.get()`. The draft does
`checkout_session.get("metadata", {}).get("response_id")` and
`refund_object.get("balance_transaction", {})`, and both raise:

```
AttributeError: 'get' is a dict method, but a Session is not a dict. Use .to_dict() to convert it.
```

This is worth knowing because it is a *loud* failure, not a silent one — but it happens on
every single webhook, so the endpoint is 100% broken. The fix is to use the SDK's typed
attribute access throughout (`obj.id`, `obj.metadata`, `obj.amount_total`, `obj.payment_status`)
or to call `obj.to_dict()` once at the top of the handler and work with plain dicts after
that. The typed attributes are better: they're checked by the SDK, and `obj.metadata` is
always a dict, so there's no `or {}` dance.

**The webhook would not be idempotent, and would not survive a retry.** The handler
pre-checks for an existing purchase and returns early if found. That is a read-then-write
race, and idempotency should come free from the schema's UNIQUE constraints —
`stripe_checkout_session_id` and `stripe_payment_intent_id` are both UNIQUE, so
`INSERT ... ON CONFLICT DO NOTHING` makes Stripe's retries harmless. The pre-check has to go.

**The refund path would not revoke anything.** It looks up the entitlement by
`Entitlement.stripe_payment_intent_id == payment_intent_id` — a column that doesn't exist —
and the fallback looks up the *purchase* by `stripe_payment_intent_id == charge_id`, which
compares a payment-intent id against a charge id. Both miss. `CLAUDE.md` calls out exactly
this failure mode: *"Refunds must stamp `revoked_at` on entitlements. `purchases.refunded_at`
alone does not revoke access — the gate reads `entitlements`."* A refund that half-works is
worse than one that doesn't, because the money is back and the access is still there.

**The webhook returns the wrong status for a bad signature.** A failed signature verification
raises `HTTPException(401)`. Stripe treats any non-2xx as a failed delivery and retries for
up to three days, so a forged request would be retried repeatedly. A 400 is the right answer
for "this body isn't for me": it stops the retries. (400 for a malformed body, 401 or 400 for
a bad signature — pick one and be consistent; the important thing is not 5xx.)

**The price is hard-coded in two places.** `unit_amount: 2999` in the checkout call and
`amount=2999` in the webhook — and `tests/seed.py` seeds a purchase with a *third* figure,
`2900`. The seed's number doesn't have to match (it's a fixture, not a price), but the
production two have to become one. It has to be one number, in one place, and the webhook
must record what Stripe actually charged rather than what we intended to charge — a discount
code, a currency change or a tax-inclusive total would otherwise be recorded wrong.
`purchases.amount_cents` is `NOT NULL` and is the record of what was paid, so it must come
from the session's `amount_total`.

**The error handler would itself 500.** The draft catches `stripe.error.APIError` and formats
`error.http_status`, `error.code` and `error.msg`. The first two exist; **`.msg` does not** —
`stripe.error.StripeError` exposes `user_message`, `code`, `http_status`, `http_body`,
`json_body`, `request_id` and `headers`, but no `msg`. So the handler raises
`AttributeError: 'APIError' object has no attribute 'msg'` while trying to report a Stripe
failure, turning a 502 into a 500 with a confusing traceback. Use `error.user_message` (or
`str(error)`), and log the `request_id` — that's the field Stripe support asks for.

**The success and cancel URLs are hard-coded to `https://crystalball.app`.** That breaks
every other origin in `ALLOWED_ORIGINS` — the Vite dev server, a tunnel, a staging box. The
project already solved this problem for OAuth: `app/origins.py` picks the configured origin
that matches the incoming request, and the rule is *"a header never becomes an origin
directly; it only selects from the list."* The checkout URLs must be built the same way, or
a dev paying in a tunnel gets bounced to production.

Note that `app/origins.py` has two relevant helpers and they behave differently:
`origin_for(request)` *falls back* to the first configured origin, while
`oauth_redirect_uri(request)` *raises* `UnlistedOriginError` for an unlisted host. Checkout
wants the forgiving one — a request from an unlisted host should still get a working (if
slightly wrong) URL rather than a 500 — but it should be a deliberate choice, and the
fallback's warning log is worth reading when it happens.

**`response_model=dict` returns the whole Stripe session object.** That leaks Stripe internals
into the frontend contract and pins the API shape to a third party's serialisation. The
frontend needs one field: the URL to redirect to. A typed `CheckoutOut` with a `url` is the
whole contract.

**`import stripe` appears twice**, once at module scope and once inside the function, and the
module-level import is placed in the middle of the import block (which is why `ruff` complains
about import sorting).

---

### Step 1 — A Stripe module, and a client

New file `app/stripe_client.py` (or `app/payments/`), holding:

- **The client**, built from `settings.stripe_secret_key` and reused across calls. Setting
  `stripe.api_key` globally is the old style; the modern SDK offers
  `stripe.StripeClient(api_key)`. Either is fine, but it should be *one* function that returns
  the client, so the key is read in one place and a missing key is a single, well-named error.

  If `StripeClient` is used, note one wrinkle: `client.checkout` is deprecated in favour of
  `client.v1.checkout` (the SDK emits a `DeprecationWarning` on the old path). The calls are
  `client.v1.checkout.sessions.create(params)` and, for retrieving a session on the return
  path, `client.v1.checkout.sessions.retrieve(id)`. The webhook side has no client method at
  all — `stripe.Webhook.construct_event` is a module-level static method — so the webhook
  handler doesn't need a client, only the secret.
- **`PaymentsNotConfiguredError`** — raised when the key is absent, mirroring
  `EmailNotConfiguredError` in `app/email.py` and `GoogleNotConfiguredError` in `app/auth.py`.
  The call site turns it into a 503 with a message naming the missing setting, which is what
  `app/routes/auth.py` already does for sign-in.
- **The price**, as a single source of truth. `REPORT_PRICE_CENTS = 2999` with the currency
  beside it, as a **setting** rather than a constant in code — so a price change is a deploy,
  and so the amount shown to the user can't drift from the amount charged. The checkout call
  passes `STRIPE_PRICE_ID` (see below), so the charged amount comes from Stripe; the setting
  is what the API *displays*, and it must be kept in step with the Stripe Price. **One
  source, two consumers** — and the check that they agree is on the rollout list.
- **A `metadata` builder**: `{"response_id": ..., "user_id": ...}`. Both, always — the webhook
  reconstructs who paid for what from the metadata, and the response id alone isn't enough
  because the response's owner can change (signing in re-points a response at a verified
  account — see `app/auth.py:claim_responses`).

**Also set `stripe_price_id`.** `purchases.stripe_price_id` exists precisely so the SKU
definition lives in Stripe rather than in a local catalogue. Using inline `price_data` (as the
removed draft did) creates an ad-hoc Price on every checkout and leaves that column null.
Create a Price in the Stripe dashboard, put its id in a setting (`STRIPE_PRICE_ID`), and pass
it as `line_items=[{"price": ..., "quantity": 1}]`. The amount then comes *from* Stripe on the
way back, which is what makes `amount_cents` trustworthy.

### Step 2 — Checkout

`POST /api/responses/{response_id}/checkout` → a Stripe Checkout session.

**Gating, in this order:**

1. `may_access_response(session, request, response_id)` — and 404 rather than 403 for an
   unowned response, same as every other route touching someone's data. The body must match
   the genuine not-found body, so a stranger can't tell an existing id from a fictional one.
2. Load the response. 404 if it's gone.
3. **Already entitled → 409 or 402, not a new checkout session.** The removed draft raised
   403 "you already have access to this report". A 403 is the wrong shape here: it isn't an
   authorisation failure, it's a conflict with the current state. `409 Conflict` is the honest
   status. The frontend can then show "you already have this" instead of sending someone to
   a checkout page for something they own.
4. Stripe not configured → 503, naming the missing setting.

**`purchases.user_id` is NOT NULL, and that's fine** even for a visitor who hasn't signed in:
the capture form already created their `users` row, and the response carries `user_id`. The
purchase is attached to that account, which is what makes the entitlement reachable from
another device after sign-in.

**The URLs are built from the request's origin**, via `app/origins.py` — the same helper the
OAuth callback uses. `success_url` carries `{CHECKOUT_SESSION_ID}` so the return page can
verify the session server-side; `cancel_url` returns to the report. Both must be built from
a *configured* origin, never from a raw header.

**Discount codes.** `discount_codes` has `stripe_coupon_id`, so a code maps to a Stripe Coupon
and is applied as `discounts=[{"coupon": stripe_coupon_id}]`. Two rules:

- Validate the code against `discount_codes` *before* creating the session, and 400 on an
  unknown code — the user should learn it's wrong before they're on Stripe's page.
- `allow_promotion_codes=True` can be left on as well, so a Stripe-side promotion code also
  works. The two mechanisms are compatible; the local table exists for admin-side
  searchability and for validating a code up front, exactly as `docs/schema.md` says.
- **A discount must not change the entitlement.** A coupon changes the amount charged; the
  access granted is the same. The webhook records the *actual* `amount_total`.

**The lookup must be case-insensitive.** The removed draft queried
`where(DiscountCode.code == body.discount_code)`, but the uniqueness is on `upper(code)` —
`CREATE UNIQUE INDEX discount_codes_code_upper_idx ON discount_codes (upper(code))` — and
`docs/schema.md` is explicit that redemption must query `WHERE upper(code) = upper($1)`. A
plain equality lookup makes the index unusable *and* makes `SUMMER20` and `summer20`
different codes, which is not what the schema intends. The query should be
`func.upper(DiscountCode.code) == body.discount_code.strip().upper()`.

This is a small thing that would otherwise ship as a latent bug: the index exists precisely
so this lookup is fast and unambiguous, and using it wrong is the kind of thing that only
shows up when two codes differing in case are created.

**Response shape.** A typed body, not `dict`:

```python
class CheckoutOut(BaseModel):
    url: str          # where to send the browser
    session_id: str   # so the return page can verify it
```

**The price has to be readable before checkout.** The paywall teaser needs to say what the
report costs *before* the visitor commits to a checkout, so the price can't only exist
inside the Stripe session. Either a small `GET /api/pricing` returning
`{"amount_cents": 2999, "currency": "usd"}`, or the price on the questionnaire response the
frontend already fetches. The former is simpler and has one obvious home; the latter saves
a round trip. Either way the number comes from the server — **not hard-coded in the
frontend** — for the same reason pay figures come from the `career_pay` lookup and are never
fabricated.

### Step 3 — The webhook

`POST /api/stripe/webhook`, verifying the signature with `stripe_webhook_secret`.

**Signature verification.** `stripe.Webhook.construct_event(raw_body, sig_header, secret)`.
Note that the raw body must be read as bytes *before* any JSON parsing — FastAPI's
`await request.body()` gives the exact bytes Stripe signed, and re-serialising parsed JSON
would change the bytes and break the signature. The SDK's `verify_header` decodes bytes to
`str` before signing, so passing the bytes directly is correct.

`construct_event` raises `stripe.error.SignatureVerificationError` for a bad signature, a
missing header *and* a missing secret, and `ValueError` for a body that isn't JSON. It also
enforces a default tolerance of five minutes on the timestamp, so a replayed event is
rejected.

The returned event's `data.object` is a typed SDK object — `stripe.checkout.Session` for
`checkout.session.completed`, `stripe.Charge` for `charge.refunded` — **not a dict**. Use its
attributes (`obj.id`, `obj.metadata`, `obj.amount_total`, `obj.payment_status`,
`obj.payment_intent`). These classes have no `.get()`; calling it raises
`AttributeError: 'get' is a dict method, but a Session is not a dict`.

One gotcha worth knowing: `construct_event` also builds a `stripe._APIRequestor`, which wants
an API key. With no key configured it still works for local parsing (it defaults to
`stripe.api_key`, which is `None`), but set the key first anyway so nothing surprises you
later.

**`checkout.session.completed` → purchase + entitlement, in one transaction.**

```
BEGIN
  INSERT INTO purchases (...) ON CONFLICT (stripe_checkout_session_id) DO NOTHING
  -- if no row was inserted, this is a retry: stop here, the work is already done
  INSERT INTO entitlements (user_id, kind, purchase_id, response_id, valid_from)
    VALUES (:user, 'single_report', :purchase, :response, now())
COMMIT
```

Idempotency comes free from the schema, as the plan says. `stripe_checkout_session_id` and
`stripe_payment_intent_id` are both UNIQUE, so `ON CONFLICT DO NOTHING` makes Stripe's
retries harmless. **Write the test that fires the same event twice and asserts one
entitlement** — this is the bug that silently double-grants.

Three details that matter:

- **`payment_status` must be `paid`.** A `checkout.session.completed` event also fires for
  sessions that are merely *created* in some flows, and for `async` payment methods the
  session completes before the money has actually arrived. Only grant access when
  `payment_status == "paid"`. For a card, `checkout.session.completed` with `payment_status:
  "paid"` is the right trigger. (`checkout.session.async_payment_succeeded` is the later
  event for delayed methods — worth handling, worth a comment saying why it's safe to skip
  for cards.)
- **The amount comes from the session**, `amount_total`, not from a constant. `purchases.
  amount_cents` is `NOT NULL` and is the record of what was paid. `amount_total` is already
  net of the discount, so a coupon'd purchase records what was actually charged.
- **`purchases.paid_at`** is stamped from the session's `created` or from `now()`. It exists
  and is currently never written by anything.
- **The metadata's `user_id` must be checked against the response's actual owner.** The
  metadata carries both `response_id` and `user_id` so the webhook can reconstruct who paid
  for what. But `purchases.user_id` and `entitlements.user_id` must agree with
  `questionnaire_responses.user_id` — the gate looks up entitlements *by user*, so an
  entitlement attached to the wrong user is an entitlement nobody can use, and one attached to
  a user who doesn't own the response is an access-control hole. **Read the response row and
  use its `user_id`**, rather than trusting the metadata's copy. The metadata's `user_id` is
  then a cross-check (it must match, or something has gone wrong and the event should be
  logged and refused), not the source of truth.

  This matters because `app/auth.py:claim_responses` re-points a response at a verified
  account when a visitor signs in. A response can therefore be owned by a different user at
  webhook time than it was at checkout time. The entitlement should follow the *response's
  owner*, so the person who ends up with the report is the one who can use it.

**The entitlement's shape must satisfy the CHECK constraints:**

- `kind = 'single_report'` requires `response_id IS NOT NULL` — so the response id must come
  from the metadata, and a session whose metadata names a response that no longer exists has
  to be handled (log it, don't grant access).
- `entitlements_origin` requires **exactly one** of `purchase_id` / `granted_by`. A purchased
  entitlement sets `purchase_id` and leaves `granted_by` null. Verified: an entitlement with
  neither raises `CheckViolationError: violates check constraint "entitlements_origin"`.
- `valid_from` has a server default of `now()`, so it can be left unset. Verified: leaving it
  null applies the default. (Passing it explicitly is fine too.)
- `kind = 'single_report'` with `valid_until = NULL` is valid — the shape constraint only
  requires an expiry for `window`.

**`charge.refunded` → stamp `purchases.refunded_at` **and** `revoked_at` on the entitlement
that purchase issued.** `CLAUDE.md` calls this out and it's genuinely easy to half-do: the
gate reads `entitlements`, so a refund that only touches `purchases` revokes nothing. The
lookup key has to be the one that exists: find the purchase by `stripe_payment_intent_id`,
then find the entitlement by `entitlement.purchase_id == purchase.id`. Not by a payment
intent id on the entitlement — **that column doesn't exist**. The entitlement is linked to its
purchase by `purchase_id`, which is exactly what the `entitlements_origin` check is for.

A refund can also arrive as `refund.created` (a partial refund) or `charge.refunded`. The
plan names `charge.refunded`; handle the full-refund case there, and note in a comment that
partial refunds are not yet distinguished.

**Webhook responses.** 200 for anything handled (including event types we ignore — Stripe
retries anything non-2xx, and a well-formed event we don't care about is not a failure). 400
for a body that isn't a valid signed event. 503 if the webhook secret isn't configured, so
the failure is loud rather than silent.

The draft returns **401** for a bad signature. That is not wrong in the abstract — 401 is
defensible for "you are not who you say you are" — but Stripe's behaviour is what matters
here: it retries any non-2xx delivery for up to three days, and it treats 400 and 401
differently from 5xx. A 400 stops the retries; a 401 does not in the same way. **Use 400 for
a body that fails signature verification**, because the practical question is "should Stripe
keep sending me this?", and the answer for a forged or malformed body is no. Reserve 5xx for
"we couldn't process a legitimate event" — that's the case where Stripe *should* retry.

**Do not leak the Stripe error message to the client.** The draft returns
`f"invalid payload: {error}"`, which is the SDK's internal message. It leaks nothing
secret, but the response body for a webhook should be a fixed string; a webhook error is
for the logs, not for the caller. Log the detail, return the status.

**`stripe_events` — deferred.** A table keyed on Stripe's event id would dedupe across event
*types*, but the UNIQUE constraints on `purchases` already cover the case that matters
(double-granting). Adding a table means a hand-written migration, a model, and a drift-test
update, for a belt-and-braces guarantee. **Decision: don't build it in Phase 2.** If a
double-grant ever appears in production, that's when it earns its place.

**A `stripe_events` alternative that's free:** Stripe sends the same event id on retries, and
the `checkout.session.completed` handler is already idempotent through the purchase UNIQUE
constraint. Nothing more is needed.

### Step 4 — The entitlement the purchase grants

This is the part that connects the money to the product, and it is where the plan's most
important rule lives: **paid reports are generated server-side only after the entitlement gate
query returns a row.**

Today, `submit_section` calls `generate_report(session, response.id)` with no `tier` argument
(`app/responses.py:233`), so every report is `tier='free'`. The `full` tier is modelled in the
enum and the column, and never issued — `docs/features.md` says so.

Two things should follow from a purchase:

1. **The entitled sections become answerable.** `sections_for()` already returns
   `ALL_SECTIONS` when the gate query returns a row, so the *sections* work as soon as the
   entitlement exists. This needs no change.
2. **The report generated afterwards should be `tier='full'`.** `generate_report()` takes a
   `tier` argument and every caller passes the default. The submit path should pass
   `ReportTier.FULL` when the response has an active entitlement, and `ReportTier.FREE`
   otherwise. This is a small change with a visible effect: the report says what it is, and
   `reports.tier` becomes meaningful rather than a constant.

Note that `generate_report` *appends* — "Appends — never overwrites an earlier one" — so a
free report already exists by the time the user buys, and a `full` report is a second row.
That's the intended shape (the `reports` table has no uniqueness constraint on `response_id`),
and the frontend already handles it: `Questionnaire.tsx` navigates to the `report_id` the
submit response returns, so it shows the newest report. `/api/auth/me` likewise lists reports
`ORDER BY generated_at DESC`. **No change needed** — but it's worth writing down, because
"the report the user is looking at" becomes a real question the moment buying can create a
second report for the same response, and the answer should be "the newest one", not "the
first one" or "the free one".

**The ordering problem.** The webhook grants the entitlement. The user is redirected back
immediately. They may click "continue" before the webhook has landed. If the submit route
checks the gate and the entitlement isn't there yet, the paid sections are refused and the
user — who has just paid — sees a 403. Two answers, and the plan should pick one:

- **Verify the Checkout session server-side on return.** The return page calls an endpoint
  that retrieves the session from Stripe, confirms `payment_status == "paid"`, and — if the
  webhook hasn't landed — performs the same purchase+entitlement write inline. This is the
  robust answer, and it also fixes the case where a webhook is delayed or lost.
- **Poll.** Cheaper, and the plan mentions it. But polling a `useQuery` for an entitlement
  that may not exist yet is a poor fit for a first implementation, and it leaves the failure
  case (webhook permanently lost) unhandled.

**Recommendation: verify on return.** One endpoint, `GET /api/checkout/{session_id}`, which
retrieves the session from Stripe, checks the metadata matches the signed-in user's response,
and returns whether access is now granted. It shares the write path with the webhook — one
function, `grant_purchase(session, checkout_session)`, called by both. That's the key design
point: **the webhook and the return-page check call the same function**, so there's one
implementation of "a completed checkout becomes a purchase and an entitlement", and the return
path can be trusted even if the webhook is late.

Note that this makes the "verify server-side on return" answer cheap, and it makes the
"poll" answer unnecessary.

### Step 5 — Tests

The money path needs tests that don't touch Stripe's network. The pattern the project already
uses is to replace the thing that talks to the outside world: `app/email.py`'s `_deliver` is
replaced in `tests/test_email.py`, and `public.email_report_task` is monkeypatched. The same
applies here.

**What to test:**

| Test | What it protects |
| --- | --- |
| Checkout on an unowned response → 404 | The 404-not-403 rule, on the new route |
| Checkout when Stripe isn't configured → 503 | The "integrations are optional" contract |
| Checkout when already entitled → 409 | Not sending someone to pay for what they own |
| Checkout with an unknown discount code → 400 | Failing before they're on Stripe's page |
| Checkout returns a `url` and a `session_id` | The typed contract |
| Webhook with a bad signature → 400, no rows written | Signature verification |
| Webhook with no signature → 400 | Ditto |
| Webhook `checkout.session.completed` → one purchase, one entitlement | The happy path |
| **The same event twice → still one purchase, one entitlement** | **The double-grant bug** |
| Webhook for a session with no metadata → no rows, no crash | A malformed session |
| Webhook `charge.refunded` → `purchases.refunded_at` **and** `entitlements.revoked_at` | The half-done refund |
| After the refund, the gate returns False | The end-to-end consequence of the above |
| The entitled sections become answerable | The gate → product connection |

**Building a signed webhook payload.** The Stripe SDK ships the helper for exactly this:
`stripe.WebhookSignature.generate_signature_header(payload, secret)` returns a
`Stripe-Signature` header value. It's a public, documented API — *"Useful for signing payloads
in unit tests."* Verified working:

```python
payload = json.dumps(event_dict)
sig = stripe.WebhookSignature.generate_signature_header(payload, secret)
# "t=1792534654,v1=2ed5c22b1e26859110f2bab989a4c0cc6ea01a3e6657aca2862da2331c07b3b8"
client.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": sig})
```

So the test can build a real event body, sign it with a test secret, and post it to the
route. The signature verification is then genuinely exercised rather than bypassed. No
mocking of `construct_event` needed. Note that the helper signs the *string* — pass the same
exact string as the request body, and the signature will verify.

**The event body shape.** `construct_event` requires a well-formed Stripe event: `id`,
`object: "event"`, `type`, and `data.object` with its own `object` discriminator
(`"checkout.session"`, `"charge"`). A body missing those fails to parse as an event, so the
test fixture has to include them.

**Conftest additions.** The project rule is that no test may read ambient configuration — every
setting a test depends on is forced in `conftest.py`. Phase 2 adds two:

- A `stripe_configured` fixture (opt-in, not autouse) that sets `STRIPE_SECRET_KEY` and
  `STRIPE_WEBHOOK_SECRET` to test values and clears the settings cache — the same
  `get_settings.cache_clear()` dance the existing `locked` and `origins` fixtures use.
- An autouse default that clears them, so a developer's `.env` — which *has* real Stripe keys
  in it — can't change what a test does. **The existing `.env` has live Stripe keys in it**,
  so this is not hypothetical: without the autouse fixture, a test that asserts "503 when
  Stripe isn't configured" would pass on the author's machine and fail in CI.

**A Stripe fake for the outbound calls.** The checkout route calls
`stripe.checkout.Session.create`. Replace it in tests — `monkeypatch.setattr` on the module
attribute, the same shape as `email_module._deliver` in `tests/test_email.py`. The fake
returns a small object with `id`, `url`, `amount_total`, `payment_status`, `metadata` and
`payment_intent` — enough for both the checkout route and the webhook handler.

### Step 6 — Documentation

- **`docs/features.md`** — move Stripe from "Deliberately absent" to the client-facing
  section; describe the checkout flow, the refund behaviour and the entitlement semantics.
  Update the "Built but dormant" table: `full` report tier is no longer dormant if Phase 2
  issues it.
- **`docs/schema.md`** — the "Auto-renewal is deferred" and "Open questions" sections still
  carry the unanswered question about auto-renewing vs a bounded window. Whatever is decided
  gets recorded there. The schema doc also says the `discount_codes` table "may turn out to
  be unnecessary; revisit at Stripe implementation" — that's this implementation, so say what
  was decided.
- **`CLAUDE.md`** — the "Entitlement Gate" section is thin: it describes the gate but not
  where the *purchase* side lives, who writes entitlements, or that the report tier follows
  the entitlement. Add the short version.

  It also contains a small inaccuracy worth fixing while you're there: it says
  `single_report` "expires after use". It doesn't. The gate query
  (`app/entitlements.py`) checks `revoked_at IS NULL` and the `valid_from`/`valid_until`
  window — and never `consumed_at`. Nothing anywhere sets `consumed_at`; it exists for the
  `coaching_session` kind, which the schema doc says is a placeholder. The shape constraint
  also makes `valid_until` optional for `single_report`, so a bought report is permanent
  until refunded. Fix the description to match the code, because a future reader will
  otherwise build on the belief that a report expires and stop worrying about revocation.
- **`README.md`** — how to set up Stripe (test keys, the webhook endpoint, `stripe listen`),
  and what happens when the keys are absent.
- **`docs/plan.md`** — delete or replace this file when Phase 2 lands, per the project rule
  that *"a stale plan is worse than none."*

### Step 7 — The queue: decide, don't drift

The purchase→entitlement write is the case the Postgres-queue rule was written for. Two
honest options:

- **Build it now** (~½ day) — `procrastinate` is already in `pyproject.toml` (and
  `procrastinate` is in `uv.lock`), and moving report email onto it while you're there fixes
  the non-durable email at the same time.
- **Defer it** — acceptable **only** because the webhook handler writes the entitlement
  *inline and transactionally*, so there's no job to lose on this path. Stripe retries a
  failed webhook, which covers the failure mode a queue would.

**Decision: defer.** The webhook writes the entitlement in the same transaction as the
purchase; there is no job to enqueue, so the queue buys nothing on this path. The report
*email* is a separate matter — it's currently on `BackgroundTasks` and is genuinely lossy — but
moving it is not part of the payment flow, and bundling the two makes both harder to review.

**What deferring must not become:** pretending the queue exists. `docs/requirements.md` and
`CLAUDE.md` both say "not built yet", and they should keep saying so. If Phase 2 later needs
a durable job (e.g. emailing a receipt, or a report regeneration triggered by a purchase),
that's when the queue gets built, with the purchase enqueueing in the same transaction.

---

## Phase 3 — The frontend

*Done. The decisions below are as they were made, and the implementations are recorded in
"Phase 3 — done" above.*

Phase 3 depends on the API shape from Phase 2, so it isn't detailed here. The decisions worth
making now:

- **The upgrade CTA** on the paywall teaser (`frontend/src/components/report/PaywallTeaser.tsx`,
  currently a disabled button saying "Payments aren't enabled yet").
- **Redirect to Checkout** — the browser leaves the app, so this is a full-page navigation,
  not a fetch. `api/client.ts` already has the pattern for this in `googleLoginUrl`: *"A URL,
  not a fetch. Sign-in is a full-page navigation to Google and back."* The checkout URL comes
  from the API, so this one is a POST-then-navigate.
- **The return race.** Stripe redirects the browser back immediately, but the webhook may land
  a second or two later. Phase 2's `GET /api/checkout/{session_id}` (step 4) is what solves
  this — the return page calls it and renders "you're in" rather than assuming.
- **Return and cancel pages** — `/checkout/success` and `/checkout/cancelled`, routed in
  `App.tsx`. The cancel page returns to the report with a line saying nothing was charged.
- **Everything stays behind `src/api/`** per the layering rule in `CLAUDE.md`: the checkout
  call in `client.ts`, the type in `types.ts`, the hook in `queries.ts`. Nothing outside
  `src/api/` may call `fetch` or know a URL.
- **Price display.** The price lives on the server. The teaser should show what the server
  says it costs, not a number hard-coded in the frontend — the same reason pay figures come
  from the `career_pay` lookup and are never fabricated. This means the teaser needs the
  price from the API, which is a small addition to the checkout or a pricing endpoint.
- **Types** — `src/api/types.ts` is hand-written and drifts. It's already broken (see Phase 1
  fixes) and it will need `CheckoutOut` added. Generating it from `/openapi.json` with
  `openapi-typescript` is the standing upgrade and this is a good moment to do it, since the
  contract is changing anyway.

---

## Blocking questions

*These were open when the plan was written. Each was answered before the work, and the
answers are recorded here so the reasoning survives.*

**1. Does paid access auto-renew, or is it a bounded window users re-buy?**

It does not block starting. Build `single_report` first regardless — it's the simplest shape,
it's what a demo needs, and `window` is additive: a different `kind`, an expiry, and the gate
query handles it unchanged.

What the answer *does* change is whether Stripe Subscriptions enter the picture, which is
invoice webhooks, dunning and proration — a separate body of work, not a modification of this
one. `docs/schema.md` records that a subscription is "just a thing that *issues* `window`
entitlements on a schedule".

**Recommendation for Phase 2: `single_report` only.** Leave a `WINDOW_DAYS` constant in place
but unused, and note in the code where the switch would go.

**Answered: `single_report` only.** The gate understands a `window` entitlement and nothing
issues one. `window`, Stripe Subscriptions, auto-renewal and dunning are all out of scope.

**2. What is the price, and is it the same for everyone?**

Phase 2 needs a number. `REPORT_PRICE_CENTS = 2999` was the removed draft's figure and is
reasonable for a single-report unlock. It should be a **setting**, not a constant in code,
so a price change is a deploy and so the amount the frontend shows can't drift from the
amount Stripe charges.

Two sub-questions this opens, both answerable now and both cheap:

- **Does the price live in Stripe or in our settings?** Recommendation: the *authoritative*
  amount is the Stripe Price (`STRIPE_PRICE_ID`), because that's what the customer is
  actually charged. `REPORT_PRICE_CENTS` exists to *display* the price before checkout, and
  must match the Stripe Price. If they ever disagree, the display is wrong — so the rollout
  checklist includes checking them against each other, and a test that asserts the setting
  equals the Stripe Price's amount would need a network call, so a startup check against the
  Stripe API is more honest than a unit test here.
- **Is it the same for everyone?** For Phase 2, yes — one price. Per-user pricing would
  need a Price per cohort, which is the `discount_codes` mechanism's job, not a new one.

**Answered: one price, $29.99.** `report_price_cents = 2999` and `report_price_currency =
"usd"` are settings, and `stripe_price_id` names the Stripe Price that is actually charged.
The two must agree, and the rollout checklist checks them against each other.

**3. Is the discount-code table the source of truth, or is Stripe?**

`docs/schema.md` says the local table "may turn out to be unnecessary; revisit at Stripe
implementation." Phase 2 is that implementation.

**Recommendation: keep both, with clear roles.** Stripe owns the pricing logic (the Coupon
and its rules); the local table owns *which codes this product accepts* and lets a code be
validated before the redirect. `discount_codes.stripe_coupon_id` is the join. The local
`starts_at`/`ends_at`/`max_redemptions` columns are then a second, redundant set of rules —
**decide whether they're enforced** (a code outside its window is rejected locally) or
**left unused** (Stripe's coupon rules govern). Leaving them unenforced while the columns
exist is the worst of both: it looks like a control that isn't one.

**Recommendation: enforce `starts_at`/`ends_at` locally** and treat `max_redemptions` as
unimplemented, with a comment saying so. A code outside its window is a 400.

**Answered: as recommended.** The lookup is on `upper(code)`, the local window is enforced, and
`max_redemptions` is not — there's a comment on the function saying so. The code is applied to
Stripe as `discounts=[{"coupon": stripe_coupon_id}]`, and the webhook records what Stripe
actually charged, so a coupon changes the amount and never the access.

**4. Does the free report stay free, or does buying unlock a *different* report?**

Today buying unlocks the two remaining *sections* of the same report. That's the model in
`docs/requirements.md` and it needs no change. Worth confirming that's still what the client
expects, because a "buy the report" model (one artefact, priced) is a different product
decision and would change the shape of the checkout and the tier.

**5. Refunds — who triggers them?**

Stripe's dashboard, or a staff action in the app? `admin_permission` already has
`manage_purchases`, and `docs/schema.md` says that's "what the `manage_purchases` admin
permission actually gates" — but there is no admin UI and no admin authentication
(`docs/features.md` says so). **Phase 2 assumption: refunds are issued from the Stripe
dashboard, and the webhook does the revocation.** No admin surface is built. This is recorded
rather than decided, because it's the only option that doesn't require the admin surface.

**Answered: the Stripe dashboard.** There is no admin surface, and building one is a separate
piece of work. The `charge.refunded` webhook stamps both `purchases.refunded_at` and the
entitlement's `revoked_at`, so a refund issued from the dashboard revokes access on its own.

---

## Acceptance criteria

*Phase 2 is done when all of these are true. Each is checkable, and each maps to a test above
or a manual step in the rollout. **All of the automated ones are met** — 368 backend tests
pass, `ruff check` and `ruff format --check` are clean, and `tsc --noEmit` and the Vite
production build are clean. The manual ones are on the rollout list.*

**Correctness**

- A visitor with no entitlement gets only the interests section, on every route that serves
  sections, and cannot submit the others.
- A visitor with an entitlement gets all three sections, on the same routes.
- A completed checkout produces exactly one `purchases` row and one `entitlements` row,
  even when the same event is delivered three times.
- The purchase records the amount Stripe actually charged, and the Stripe session, payment
  intent and price ids.
- A refund stamps `purchases.refunded_at` **and** the entitlement's `revoked_at`, and the
  gate returns False afterwards. **A refund that only touches `purchases` fails this.**
- An entitlement is attached to the response's owner, not to whatever the metadata said.
- A webhook with a bad or missing signature writes nothing and returns 400.
- A webhook for an event type we don't handle returns 200 and writes nothing.
- A session with no usable metadata writes nothing and doesn't crash.

**The contract**

- `POST /api/responses/{id}/checkout` returns a typed body with a `url` and a `session_id`
  — not a Stripe object.
- The checkout URL is built from `ALLOWED_ORIGINS`, not from a header and not hard-coded.
  Paying from a dev server, a tunnel and production all return to the right place.
- An unowned response returns 404 with the same body as a missing one.
- Already entitled returns 409, not a second checkout session.
- Stripe not configured returns 503 naming the setting, and every other route still works.

**Quality**

- `uv run pytest` is green, with the double-fire and refund tests among them.
- `uv run ruff check` and `uv run ruff format --check` are clean.
- `tsc --noEmit` is clean, and `types.ts` matches the backend schemas.
- No test reads ambient configuration: the Stripe settings are forced in `conftest.py`, so
  the suite behaves the same with the developer's `.env` present and absent.
- The docs in Step 6 are updated — `features.md`, `schema.md`, `CLAUDE.md`, `README.md`.

**The post-payment path**

- A visitor who has paid can answer the paid sections without being offered the free one
  again, and without the server refusing a second submission.
- The questionnaire reports which sections are done, so the client doesn't have to guess.
- Payment alone does not create a full report, and the success page doesn't wait for one
  that can't exist yet.
- A visitor who has paid but hasn't finished sees a prompt to finish, not a paywall for
  sections they have already bought.

**Honest state**

- `docs/requirements.md` and `CLAUDE.md` still say the job queue is not built. They must not
  claim otherwise.
- `docs/features.md` no longer lists Stripe under "Deliberately absent" once it ships.

---

## Rollout

Phase 2 is deployed in two steps, because the pieces can go live independently and the
second is the one that can lose money.

**Step A — the code, with Stripe unconfigured.** Deploy with `STRIPE_SECRET_KEY` and
`STRIPE_WEBHOOK_SECRET` unset. The app starts (both settings are optional), the gate
works, and the checkout route returns 503. Nothing is charged and nothing is granted. This
is the safe way to land the money code: the failure mode is a 503, not a bad grant.

**Step B — Stripe configured.** Set the keys and the `STRIPE_PRICE_ID`, register the webhook
endpoint in the Stripe dashboard for `checkout.session.completed`,
`checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed` and
`charge.refunded`, and point it at `/api/stripe/webhook`. Then run one end-to-end purchase in
**test mode** with `stripe listen --forward-to localhost:8000/api/stripe/webhook` and check the
database afterwards: one `purchases` row, one `entitlements` row, `amount_cents` matching what
was charged.

**Step B is not yet done — it needs a Stripe account.** The Stripe CLI is installed
(`~/.local/bin/stripe`, v1.19.4) but not logged in: there is no `~/.config/stripe/config.toml`
and no API key on this machine. Everything up to that point is written and tested; what
remains is a live purchase, and it needs credentials that only the account holder can supply.
`stripe login` opens a browser to authorise it, or `stripe login --api-key <key>` takes a key
directly.

**Before going live, check:**

- The Stripe Price id in `STRIPE_PRICE_ID` is the live price, not a test one.
- The webhook endpoint is registered for the live mode's signing secret, and
  `STRIPE_WEBHOOK_SECRET` is that secret — not the test one. **A mismatched secret fails
  every delivery**, and the symptom is a paid purchase that never grants access.
- `ALLOWED_ORIGINS` contains the production origin, so the success and cancel URLs point
  somewhere real.
- The price on the Stripe Price matches `REPORT_PRICE_CENTS` — or, better, that the setting
  is only used for display and the webhook records `amount_total` regardless.
- **A refund has been tried.** Issue one from the dashboard and check that the entitlement's
  `revoked_at` was stamped and the paid sections are refused again. The refund path is the
  easiest thing to get half-right, and a refund that doesn't revoke is worse than no refund
  handling at all.

**Not in Phase 2:** an admin surface for refunds (the dashboard is the interface), a
`stripe_events` table, the `window` pricing model, Stripe Subscriptions, and the durable job
queue. Each is listed above with the reason.

---

## Suggested order

*As it was written. All of it is done.*

1. ~~**Fix Phase 1's four failing tests, the ruff errors and the broken `types.ts`.**~~ **Done.**
   The tree is green: 368 backend tests pass, `ruff check` and `ruff format --check` are
   clean, and `tsc --noEmit` is clean.
2. **The Stripe module** (step 1): client, price, metadata, errors. (½ day)
3. **The checkout endpoint** (step 2): gating, URLs from `app/origins.py`, discount
   codes, typed response. (1 day)
4. **The webhook** (step 3): signature verification, the idempotent purchase+entitlement
   write, the refund revocation. (1 day)
5. **The tests** (step 5), including the double-fire test and the refund test.
   (1 day, overlapping with 3 and 4)
6. **The return-page verification endpoint and the `full` report tier** (step 4). (½ day)
7. **The documentation** (step 6). (½ day)
8. **Phase 3** — the frontend.

Steps 4 and 5 are deliberately interleaved: the double-fire test and the refund test are
what prove the two hardest parts of the webhook are right, and writing them after the
webhook is done means the webhook is written twice. Write the webhook and its tests
together.

**Total: about five days** for Phase 2. The extra day in the original four-to-six estimate is
the `window` pricing model, which this plan does not build.

**Then commit Phase 1 and Phase 2 separately.** Phase 1 is a coherent change on its own — the
gate and the removal of the bypass — and it shouldn't be entangled with a payment integration
in a single commit. Phase 1's commit message should say what the gate does and that the
bypass is gone, because that's the change a future reader of `git log` most needs explained.

**Phase 1 is committed** as `2f40a73`. Phases 2 and 3 are committed together, because the
frontend calls the API Phase 2 adds, and a commit whose frontend called an endpoint that
didn't exist yet wouldn't build.
