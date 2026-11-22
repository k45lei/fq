1. **Database (`backend/prisma/schema.prisma` + migration)**
* Reuse the existing `DiscountCode` model (`code`, `stripeCouponId`, `startsAt`, `endsAt`, `maxRedemptions`).
* Add `discountCodeId` (nullable FK on `Purchase`) or `redemptionsCount` on `DiscountCode` to track usage against `maxRedemptions`.


2. **Backend (`backend/src/payments.ts`)**
* Add a `resolveDiscountCode` helper to normalize the code, query `db.discountCode.findUnique`, and reject (`400`) if missing, outside `startsAt`/`endsAt`, or at `maxRedemptions`.
* In `POST /api/responses/:responseId/checkout` and `stripeGateway.create`: validate the code, pass `discounts: [{ coupon: discount.stripeCouponId }]` and `metadata.discount_code_id` to `checkout.sessions.create` (omit `allow_promotion_codes`), and include the code in `idempotencyKey`.
* In `fulfill()`: record `discountCodeId` / increment `redemptionsCount` inside the transaction.
* *(Optional for 100%-off codes without Stripe)*: Implement `POST /api/responses/:responseId/apply_discount` to grant `Entitlement` directly when `stripeCouponId` is `null` (requires making `Entitlement.purchaseId` nullable or creating a `$0` `Purchase`).


3. **Frontend (`frontend/src/components/report/PaywallTeaser.tsx`)**
* Add a `discountCode` input near the payment button and pass it to `useCreateCheckout()` (`api.createCheckout` already sends `{ discount_code }`). Existing `checkout.error` rendering will display `400` errors.

## Extra implementation steps

4. **Schema details (migration required)**
* `Purchase.stripeCheckoutSessionId` is required and unique — a `$0`/direct-grant path cannot reuse it. Either create a tagged synthetic session id (e.g. `discount:{codeId}:{attemptId}`) or create the `Purchase` as specified below with a synthetic value; decide explicitly.
* `Entitlement.purchaseId` is currently required with `onDelete: Restrict`. For a direct grant, either make it nullable (add `discountCodeId` FK to `Entitlement` instead, mirroring the generated-client drift) or still create a `$0` `Purchase`. Pick one; both need a migration.
* Add the chosen tracking column (`Purchase.discountCodeId` or `Entitlement.discountCodeId`) plus `DiscountCode.redemptionsCount Int @default(0)` if tracking on the code itself. Regenerate the Prisma client and check it in.
* Note: the generated client already exposes an optional `discountCodeId` on `Entitlement` and a nullable `purchaseId`, but `schema.prisma` does not — regenerate from the agreed schema rather than trusting either.

5. **Redemption counting and race safety**
* `fulfill()` and `apply_discount` must increment the redemption counter inside the same transaction that grants the entitlement, with an atomic guarded update (e.g. `updateMany({ where: { id, OR: [{ maxRedemptions: null }, { redemptionsCount: { lt: maxRedemptions } }] }, ... })`) and abort if it matches 0 rows. A plain read-then-increment check races.
* Use the same `lockAttempt` + advisory-lock ordering as `fulfill()` so discount grants serialize against concurrent checkout/webhook for the same attempt.

6. **Validation rules to pin down**
* Code normalization (trim + case-insensitive lookup vs stored case).
* Per-attempt reuse: reject re-applying a code to an already-entitled attempt (the checkout route already throws 409 when `entitled`); decide whether the same applies to `apply_discount`.
* 100%-off detection: define it as `stripeCouponId === null` vs a `percentOff === 100` coupon, and reject `apply_discount` for any code that has a non-100% Stripe coupon (those must go through Checkout).
* Decide the error shape (`400` for invalid/expired/exhausted, `409` for already-entitled) and mirror it in the frontend's existing `ApiError` handling.

7. **`apply_discount` endpoint contract**
* Parse the body with the same zod `.strict()` pattern as `checkoutBody` (reuse `{ discount_code }` schema).
* Response body is currently typed `void` on the frontend (`api.applyDiscount` → `request<void>`) but the route throws 501 and returns nothing on success — define a real response (e.g. `{ entitled: true }`) and update `applyDiscount`'s return type, or keep `204` and keep it void.
* On success the frontend only invalidates `["questionnaire"]`; also invalidate `latestReport`/`me` if the UI shows entitlement-dependent state elsewhere.

8. **Frontend wiring actually needed**
* `PaywallTeaser` needs local state for the code input and must pass `discountCode` into `useCreateCheckout` (already plumbed through `api.createCheckout` and the mutation) and, for 100% codes, call `useApplyDiscount` instead of redirecting.
* Decide UX for mixed case: try checkout with code, fall back to `apply_discount` on a specific error code, or a separate "Apply code" button for 100% codes. Don't auto-fallback on any 400.

9. **Tests and docs**
* Add integration tests (mirroring `backend/test/integration.test.ts`): valid code, expired code, exhausted code, wrong-attempt reuse, 100% direct grant, partial coupon via checkout, webhook fulfillment with `discountCodeId` recorded, concurrent redemption race.
* Update `backend/README.md` (discount endpoints, env/Stripe coupon setup) and `docs/verification.md`; update `frontend` mocks if any assert on `PaywallTeaser`.
