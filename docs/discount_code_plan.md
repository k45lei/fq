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
