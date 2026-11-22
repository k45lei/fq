import Stripe from 'stripe';
import { z } from 'zod';
import type { FastifyInstance } from 'fastify';
import type { Config } from './config.js';
import { type PrismaClient, type Tx, lockAttempt } from './db.js';
import { access, entitled } from './auth.js';
import { HttpError, uuid } from './domain.js';

export interface Checkout {
  id: string;
  responseId: string;
  priceId: string;
  paymentIntentId: string | null;
  paid: boolean;
  fullyRefunded: boolean;
  amount: number;
  currency: string;
  discountCodeId: string | null;
}

export interface PaymentGateway {
  price(): Promise<{
    amount_cents: number;
    currency: string;
  }>;
  create(
    attemptId: string,
    userId: string,
    discount?: { couponId: string; codeId: string },
  ): Promise<{
    url: string;
    session_id: string;
  }>;
  retrieve(id: string): Promise<Checkout>;
  event(
    body: Buffer,
    signature: string,
  ): {
    type: string;
    objectId: string;
    paymentIntentId?: string;
  };
  refunded(paymentIntentId: string): Promise<boolean>;
}

export const stripeGateway = (config: Config): PaymentGateway => {
  let instance: Stripe | undefined;
  const client = () => {
    if (!config.STRIPE_SECRET_KEY || !config.STRIPE_PRICE_ID) {
      throw new HttpError(503, 'Payments are unavailable');
    }

    return (instance ??= new Stripe(config.STRIPE_SECRET_KEY, {
      timeout: 10000,
      maxNetworkRetries: 1,
    }));
  };

  const fullyRefunded = async (id: string) => {
    const intent = await client().paymentIntents.retrieve(id, { expand: ['latest_charge'] });
    const charge = intent.latest_charge;

    if (!charge || typeof charge === 'string') {
      return false;
    }

    return charge.refunded || (charge.amount > 0 && charge.amount_refunded >= charge.amount);
  };

  return {
    async price() {
      const price = await client().prices.retrieve(config.STRIPE_PRICE_ID);

      if (price.unit_amount === null || !price.active || price.type !== 'one_time') {
        throw new HttpError(503, 'Payment price is unavailable');
      }

      return {
        amount_cents: price.unit_amount,
        currency: price.currency,
      };
    },

    async create(attemptId, userId, discount) {
      const session = await client().checkout.sessions.create(
        {
          mode: 'payment',
          line_items: [
            {
              price: config.STRIPE_PRICE_ID,
              quantity: 1,
            },
          ],
          ...(discount ? { discounts: [{ coupon: discount.couponId }] } : {}),
          metadata: {
            app: 'futurequest',
            response_id: attemptId,
            user_id: userId,
            price_id: config.STRIPE_PRICE_ID,
            ...(discount ? { discount_code_id: discount.codeId } : {}),
          },
          success_url: `${config.APP_ORIGIN}/checkout/success?session_id={CHECKOUT_SESSION_ID}`,
          cancel_url: `${config.APP_ORIGIN}/checkout/cancelled?response_id=${attemptId}`,
        },
        {
          idempotencyKey: `checkout:${attemptId}:${config.STRIPE_PRICE_ID}:${discount?.codeId ?? 'none'}:${Math.floor(Date.now() / 1800000)}`,
        },
      );

      if (!session.url) {
        throw new HttpError(502, 'Checkout URL is unavailable');
      }

      return {
        url: session.url,
        session_id: session.id,
      };
    },

    async retrieve(id) {
      const session = await client().checkout.sessions.retrieve(id, { expand: ['line_items'] });
      const line = session.line_items?.data;

      if (
        session.mode !== 'payment' ||
        session.metadata?.app !== 'futurequest' ||
        !uuid.safeParse(session.metadata?.response_id).success ||
        line?.length !== 1 ||
        line[0].quantity !== 1 ||
        line[0].price?.id !== session.metadata?.price_id
      ) {
        throw new HttpError(422, 'Checkout does not match this application');
      }

      const intent =
        typeof session.payment_intent === 'string'
          ? session.payment_intent
          : (session.payment_intent?.id ?? null);

      if (session.amount_total === null || !session.currency) {
        throw new HttpError(422, 'Checkout is missing its total');
      }

      return {
        id: session.id,
        responseId: session.metadata!.response_id,
        priceId: line[0].price!.id,
        paymentIntentId: intent,
        paid: session.payment_status === 'paid',
        fullyRefunded: intent ? await fullyRefunded(intent) : false,
        amount: session.amount_total,
        currency: session.currency,
        discountCodeId: uuid.safeParse(session.metadata?.discount_code_id).success
          ? (session.metadata!.discount_code_id as string)
          : null,
      };
    },

    event(body, signature) {
      if (!config.STRIPE_WEBHOOK_SECRET) {
        throw new HttpError(503, 'Payment webhook is unavailable');
      }

      let event: Stripe.Event;

      try {
        event = client().webhooks.constructEvent(body, signature, config.STRIPE_WEBHOOK_SECRET);
      } catch (error) {
        if (error instanceof HttpError) {
          throw error;
        }

        throw new HttpError(400, 'Invalid Stripe signature');
      }

      const object = event.data.object as {
        id: string;
        payment_intent?: string | { id: string } | null;
      };

      return {
        type: event.type,
        objectId: object.id,
        paymentIntentId:
          typeof object.payment_intent === 'string'
            ? object.payment_intent
            : object.payment_intent?.id,
      };
    },
    refunded: fullyRefunded,
  };
};

// Normalize a user-typed code: trimmed, case-insensitive. Stored codes are matched the same way.
export const resolveDiscountCode = async (db: PrismaClient, raw: string) => {
  const normalized = raw.trim();

  if (!normalized) {
    throw new HttpError(400, 'Enter a discount code');
  }

  const code = await db.discountCode.findFirst({
    where: { code: { equals: normalized, mode: 'insensitive' } },
  });

  if (!code) {
    throw new HttpError(400, 'That discount code is not valid');
  }

  const now = new Date();

  if (code.startsAt && code.startsAt > now) {
    throw new HttpError(400, 'That discount code is not active yet');
  }

  if (code.endsAt && code.endsAt < now) {
    throw new HttpError(400, 'That discount code has expired');
  }

  if (code.maxRedemptions !== null && code.redemptionsCount >= code.maxRedemptions) {
    throw new HttpError(400, 'That discount code has been fully redeemed');
  }

  return code;
};

// Atomic guarded increment: succeeds only while the code is below its redemption cap.
// Callers that must not fail after money moved (webhook fulfillment) log instead of abort.
const redeemDiscountCode = async (tx: Tx, codeId: string) => {
  const updated = await tx.$executeRaw`
    UPDATE discount_codes
    SET redemptions_count = redemptions_count + 1
    WHERE id = ${codeId}::uuid
      AND (max_redemptions IS NULL OR redemptions_count < max_redemptions)
  `;

  return updated > 0;
};

export const fulfill = async (db: PrismaClient, gateway: PaymentGateway, sessionId: string) => {
  const initial = await gateway.retrieve(sessionId);

  if (!initial.paid) {
    return initial;
  }

  return db.$transaction(
    async tx => {
      // Same lock order as refunds. Re-read Stripe after locking to reconcile refund races.
      const lock = initial.paymentIntentId ?? initial.id;
      await tx.$queryRaw`SELECT pg_advisory_xact_lock(hashtextextended(${lock}, 0))::text`;
      await lockAttempt(tx, initial.responseId);
      const checkout = await gateway.retrieve(sessionId);

      if (!checkout.paid) {
        return checkout;
      }

      const attempt = await tx.attempt.findUnique({ where: { id: checkout.responseId } });

      if (!attempt) {
        throw new HttpError(422, 'Checkout attempt no longer exists');
      }

      const existing = await tx.purchase.findUnique({
        where: { stripeCheckoutSessionId: checkout.id },
      });

      if (existing) {
        if (checkout.fullyRefunded) {
          await tx.purchase.update({
            where: { id: existing.id },
            data: { refundedAt: existing.refundedAt ?? new Date() },
          });

          await tx.entitlement.updateMany({
            where: {
              purchaseId: existing.id,
              revokedAt: null,
            },
            data: { revokedAt: new Date() },
          });
        }

        return checkout;
      }

      const purchase = await tx.purchase.create({
        data: {
          userId: attempt.userId,
          attemptId: attempt.id,
          stripeCheckoutSessionId: checkout.id,
          stripePaymentIntentId: checkout.paymentIntentId,
          stripePriceId: checkout.priceId,
          amountCents: checkout.amount,
          currency: checkout.currency,
          refundedAt: checkout.fullyRefunded ? new Date() : null,
        },
      });

      await tx.entitlement.create({
        data: {
          userId: attempt.userId,
          attemptId: attempt.id,
          purchaseId: purchase.id,
          discountCodeId: checkout.discountCodeId,
          revokedAt: checkout.fullyRefunded ? new Date() : null,
        },
      });

      if (checkout.discountCodeId) {
        // The money has already moved, so a lost redemption race must not undo the grant.
        const redeemed = await redeemDiscountCode(tx, checkout.discountCodeId);

        if (!redeemed) {
          console.warn(`Discount code ${checkout.discountCodeId} redemption cap reached during fulfillment of ${checkout.id}`);
        }
      }

      return checkout;
    },
    { timeout: 45000 },
  );
};

export const reconcileRefund = async (
  db: PrismaClient,
  gateway: PaymentGateway,
  intent: string,
) => {
  await db.$transaction(
    async tx => {
      await tx.$queryRaw`SELECT pg_advisory_xact_lock(hashtextextended(${intent}, 0))::text`;

      // Partial refunds preserve access.
      if (!(await gateway.refunded(intent))) {
        return;
      }

      const purchase = await tx.purchase.findUnique({ where: { stripePaymentIntentId: intent } });

      // A refund arriving first is reconciled by fulfill's fresh Stripe lookup.
      if (!purchase) {
        return;
      }

      await tx.purchase.update({
        where: { id: purchase.id },
        data: { refundedAt: purchase.refundedAt ?? new Date() },
      });

      await tx.entitlement.updateMany({
        where: {
          purchaseId: purchase.id,
          revokedAt: null,
        },
        data: { revokedAt: new Date() },
      });
    },
    { timeout: 45000 },
  );
};

const checkoutBody = z.object({ discount_code: z.string().max(100).optional() }).strict();

const assertCheckoutEligible = (attempt: { interestsCompletedAt: Date | null }) => {
  if (!attempt.interestsCompletedAt) {
    throw new HttpError(409, 'Complete the free section first');
  }
};

export const paymentRoutes = (app: FastifyInstance, db: PrismaClient, gateway: PaymentGateway) => {
  app.get('/api/pricing', () => gateway.price());

  app.post('/api/responses/:responseId/checkout', async request => {
    const input = checkoutBody.parse(request.body);
    const id = uuid.parse((request.params as { responseId: string }).responseId);
    const attempt = await access(db, request, id);

    assertCheckoutEligible(attempt);

    if (await entitled(db, attempt.userId, id)) {
      throw new HttpError(409, 'This questionnaire is already unlocked');
    }

    if (!input.discount_code) {
      return gateway.create(id, attempt.userId);
    }

    const code = await resolveDiscountCode(db, input.discount_code);

    // Codes without a Stripe coupon are 100% grants applied without Stripe.
    if (!code.stripeCouponId) {
      throw new HttpError(400, 'This code is applied without payment; use the apply code endpoint');
    }

    return gateway.create(id, attempt.userId, { couponId: code.stripeCouponId, codeId: code.id });
  });

  app.post('/api/responses/:responseId/apply_discount', async request => {
    const input = checkoutBody.parse(request.body);
    const id = uuid.parse((request.params as { responseId: string }).responseId);
    const attempt = await access(db, request, id);

    if (!input.discount_code) {
      throw new HttpError(400, 'Enter a discount code');
    }

    assertCheckoutEligible(attempt);

    if (await entitled(db, attempt.userId, id)) {
      throw new HttpError(409, 'This questionnaire is already unlocked');
    }

    const code = await resolveDiscountCode(db, input.discount_code);

    if (code.stripeCouponId) {
      throw new HttpError(400, 'This code applies at checkout; enter it when paying');
    }

    return db.$transaction(
      async tx => {
        await lockAttempt(tx, id);

        if (await entitled(tx, attempt.userId, id)) {
          throw new HttpError(409, 'This questionnaire is already unlocked');
        }

        // Guard the redemption cap inside the same transaction as the grant.
        if (!(await redeemDiscountCode(tx, code.id))) {
          throw new HttpError(400, 'That discount code has been fully redeemed');
        }

        await tx.entitlement.create({
          data: {
            userId: attempt.userId,
            attemptId: id,
            discountCodeId: code.id,
          },
        });

        return { entitled: true };
      },
      { timeout: 45000 },
    );
  });

  app.get('/api/checkout/:sessionId', async request => {
    const { sessionId } = request.params as { sessionId: string };
    const initial = await gateway.retrieve(sessionId);
    await access(db, request, initial.responseId);
    const checkout = initial.paid ? await fulfill(db, gateway, sessionId) : initial;
    const attempt = await access(db, request, checkout.responseId);

    return {
      session_id: sessionId,
      response_id: attempt.id,
      paid: checkout.paid,
      entitled: await entitled(db, attempt.userId, attempt.id),
      payment_status: checkout.fullyRefunded ? 'refunded' : checkout.paid ? 'paid' : 'pending',
    };
  });

  // Scoped parser retains exact bytes without affecting the other JSON routes.
  app.register(async webhook => {
    webhook.removeContentTypeParser('application/json');
    webhook.addContentTypeParser(
      'application/json',
      { parseAs: 'buffer' },
      (_request, body, done) => done(null, body),
    );

    webhook.post('/api/stripe/webhook', async request => {
      const signature = request.headers['stripe-signature'];

      if (typeof signature !== 'string') {
        throw new HttpError(400, 'Missing Stripe signature');
      }

      const event = gateway.event(request.body as Buffer, signature);

      if (
        ['checkout.session.completed', 'checkout.session.async_payment_succeeded'].includes(
          event.type,
        )
      ) {
        await fulfill(db, gateway, event.objectId);
      }

      if (event.type === 'charge.refunded' && event.paymentIntentId) {
        await reconcileRefund(db, gateway, event.paymentIntentId);
      }

      return { received: 'ok' };
    });
  });
};
