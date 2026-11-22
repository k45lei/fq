import assert from 'node:assert/strict';
import { after, before, beforeEach, test } from 'node:test';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:net';
import EmbeddedPostgres from 'embedded-postgres';
import Stripe from 'stripe';
import { buildApp } from '../src/app.js';
import { configuration } from '../src/config.js';
import { database, type PrismaClient } from '../src/db.js';
import { seed } from '../src/seed.js';
import { linkIdentity } from '../src/auth.js';
import {
  fulfill,
  reconcileRefund,
  stripeGateway,
  type Checkout,
  type PaymentGateway,
} from '../src/payments.js';
import type { Questions } from '../src/questionnaire.js';
import type { Stage } from '../src/domain.js';

let pg: EmbeddedPostgres;
let db: PrismaClient;
let app: Awaited<ReturnType<typeof buildApp>>;
let dir: string;

const checkouts = new Map<string, Checkout>();

const gateway: PaymentGateway = {
  price: async () => ({
    amount_cents: 2999,
    currency: 'usd',
  }),
  create: async (id, _userId, discount) => {
    const session = discount ? `cs_${id}_${discount.codeId}` : `cs_${id}`;
    checkouts.set(session, {
      id: session,
      responseId: id,
      priceId: 'price_original',
      paymentIntentId: `pi_${id}`,
      amount: 2999,
      currency: 'usd',
      paid: true,
      fullyRefunded: false,
      discountCodeId: discount?.codeId ?? null,
    });

    return {
      session_id: session,
      url: `https://checkout.stripe.com/${session}`,
    };
  },
  retrieve: async (id) => {
    const result = checkouts.get(id);
    assert.ok(result, `Unknown checkout ${id}`);

    return { ...result };
  },
  event: () => {
    throw new Error('Use real signature verification in webhook test');
  },
  refunded: async (intent) =>
    [...checkouts.values()].some((c) => c.paymentIntentId === intent && c.fullyRefunded),
};

const secret = 'integration-secret-'.repeat(3);

let config: ReturnType<typeof configuration>;

test('production proxy isolates visitor rate limits and keeps health checks available', async () => {
  const productionApp = await buildApp(
    { ...config, NODE_ENV: 'production', APP_ORIGIN: 'https://example.com' },
    db,
    { gateway },
  );
  productionApp.get('/rate-limit-probe', async (request) => ({ ip: request.ip }));

  try {
    for (let index = 0; index < 180; index += 1) {
      const response = await productionApp.inject({
        url: '/rate-limit-probe',
        headers: { 'x-forwarded-for': '198.51.100.10' },
      });

      assert.equal(response.statusCode, 200);
    }

    const limited = await productionApp.inject({
      url: '/rate-limit-probe',
      headers: { 'x-forwarded-for': '198.51.100.10' },
    });
    assert.equal(limited.statusCode, 429);

    const otherVisitor = await productionApp.inject({
      url: '/rate-limit-probe',
      headers: { 'x-forwarded-for': '203.0.113.99, 198.51.100.11' },
    });
    assert.equal(otherVisitor.statusCode, 200);
    assert.equal(otherVisitor.json().ip, '198.51.100.11');

    const health = await productionApp.inject({
      url: '/health',
      headers: { 'x-forwarded-for': '198.51.100.10' },
    });
    assert.equal(health.statusCode, 200);
  } finally {
    await productionApp.close();
  }
});

before(async () => {
  // Allocate a disposable real PostgreSQL cluster, never read an ambient DATABASE_URL.
  dir = await mkdtemp(join(tmpdir(), 'futurequest-ts-test-'));
  const listener = createServer();
  await new Promise<void>((resolve) => listener.listen(0, '127.0.0.1', resolve));
  const port = (listener.address() as { port: number }).port;
  await new Promise<void>((resolve) => listener.close(() => resolve()));
  pg = new EmbeddedPostgres({
    databaseDir: dir,
    port,
    user: 'postgres',
    password: 'test-only',
    persistent: false,
    autoRehydrateSymlinks: true,
    onLog: () => {},
    onError: () => {},
  });

  await pg.initialise();
  await pg.start();
  await pg.createDatabase('futurequest_test');
  const url = `postgresql://postgres:test-only@127.0.0.1:${port}/futurequest_test`;
  const connection = pg.getPgClient('futurequest_test');
  await connection.connect();

  try {
    const migrations = new URL('../prisma/migrations/', import.meta.url);

    for (const entry of (await readdir(migrations, { withFileTypes: true }))
      .filter((entry) => entry.isDirectory())
      .sort((a, b) => a.name.localeCompare(b.name))) {
      await connection.query(
        await readFile(new URL(`${entry.name}/migration.sql`, migrations), 'utf8'),
      );
    }
  } finally {
    await connection.end();
  }

  db = database(url);
  await seed(db);
  config = configuration({
    DATABASE_URL: url,
    SESSION_KEY: 'ab'.repeat(32),
    NODE_ENV: 'test',
    RESULT_API_SECRET: secret,
    GOOGLE_CLIENT_ID: 'test',
    GOOGLE_CLIENT_SECRET: 'test',
  });

  app = await buildApp(config, db, {
    gateway,
    verifyGoogle: async () => ({
      sub: 'google-test',
      email: 'verified@example.com',
      name: 'Verified',
    }),
  });

  await app.ready();
});

after(async () => {
  if (app) {
    await app.close();
  }

  if (db) {
    await db.$disconnect();
  }

  if (pg) {
    await pg.stop();
  }

  if (dir) {
    await rm(dir, {
      recursive: true,
      force: true,
    });
  }
});

beforeEach(async () => {
  checkouts.clear();
  await db.$executeRawUnsafe(
    'TRUNCATE users, attempts, answers, answer_choices, reports, purchases, entitlements, discount_codes RESTART IDENTITY CASCADE',
  );
});

const begin = async () => {
  const result = await app.inject({
    method: 'POST',
    url: '/api/responses',
    payload: {
      name: 'Student',
      email: 'student@example.com',
    },
  });

  assert.equal(result.statusCode, 201, result.body);
  const cookie = result.cookies.find((c) => c.name === 'fq_ts_session')!;

  return {
    id: result.json().response_id as string,
    cookies: { fq_ts_session: cookie.value },
  };
};

type Guest = Awaited<ReturnType<typeof begin>>;

test('attempts store trimmed school text and normalize absent or blank values', async () => {
  for (const school of [undefined, null, '', '   ', '  Central Toronto Academy  ', 'S'.repeat(300)]) {
    const result = await app.inject({
      method: 'POST',
      url: '/api/responses',
      payload: {
        name: 'Student',
        email: 'student@example.com',
        ...(school === undefined ? {} : { school }),
      },
    });

    assert.equal(result.statusCode, 201, result.body);
    const attempt = await db.attempt.findUniqueOrThrow({
      where: { id: result.json().response_id },
    });

    assert.equal(attempt.school, school?.trim() || null);
  }
});

test('invalid school input creates neither an attempt nor a guest user', async () => {
  for (const school of ['S'.repeat(301), 123, { name: 'School' }]) {
    const result = await app.inject({
      method: 'POST',
      url: '/api/responses',
      payload: {
        name: 'Student',
        email: 'student@example.com',
        school,
      },
    });

    assert.equal(result.statusCode, 422, result.body);
  }

  assert.equal(await db.attempt.count(), 0);
  assert.equal(await db.user.count(), 0);
});

test('school stays with each attempt through Google claiming and signed-in starts', async () => {
  const guestResponse = await app.inject({
    method: 'POST',
    url: '/api/responses',
    payload: {
      name: 'Student',
      email: 'student@example.com',
      school: 'Original School',
    },
  });

  assert.equal(guestResponse.statusCode, 201, guestResponse.body);
  const guestId = guestResponse.json().response_id;
  const user = await linkIdentity(
    db,
    {
      sub: 'school-test-student',
      email: 'verified@example.com',
      name: 'Verified Student',
    },
    [guestId],
  );
  const cookie = app.encodeSecureSession(app.createSecureSession({ userId: user.id }));
  const nextResponse = await app.inject({
    method: 'POST',
    url: '/api/responses',
    cookies: { fq_ts_session: cookie },
    payload: {
      name: 'Unverified Name',
      email: 'unverified@example.com',
      school: '  New School  ',
    },
  });

  assert.equal(nextResponse.statusCode, 201, nextResponse.body);
  const original = await db.attempt.findUniqueOrThrow({ where: { id: guestId } });
  const next = await db.attempt.findUniqueOrThrow({
    where: { id: nextResponse.json().response_id },
    include: { user: true },
  });

  assert.equal(original.school, 'Original School');
  assert.equal(original.userId, user.id);
  assert.equal(next.school, 'New School');
  assert.equal(next.userId, user.id);
  assert.equal(next.user.name, 'Verified Student');
  assert.equal(next.user.email, 'verified@example.com');
});

const answers = async (guest: Guest, stage: Stage) => {
  const attempt = await db.attempt.findUniqueOrThrow({ where: { id: guest.id } });
  const qs = (attempt.questionnaireSnapshot as unknown as { questions: Questions }).questions;

  return qs
    .filter((q) => q.category === stage)
    .map((q) => ({
      question_id: q.id,
      ...(q.answer_type === 'scale'
        ? { numeric: q.scale_min! }
        : q.answer_type === 'free_text'
          ? { text: 'I enjoy learning new things.' }
          : { option_ids: q.options.slice(0, q.min_selections ?? 1).map((option) => option.id) }),
    }));
};

const submit = async (guest: Guest, stage: Stage) => {
  return app.inject({
    method: 'POST',
    url: `/api/responses/${guest.id}/sections/${stage}`,
    cookies: guest.cookies,
    payload: { answers: await answers(guest, stage) },
  });
};

const buy = async (guest: Guest) => {
  const checkout = await app.inject({
    method: 'POST',
    url: `/api/responses/${guest.id}/checkout`,
    cookies: guest.cookies,
    payload: {},
  });

  assert.equal(checkout.statusCode, 200, checkout.body);
  const status = await app.inject({
    url: `/api/checkout/${checkout.json().session_id}`,
    cookies: guest.cookies,
  });

  assert.equal(status.statusCode, 200, status.body);
  assert.equal(status.json().entitled, true);

  return checkout.json().session_id as string;
};

const payload = {
  holland_code: [],
  traits: {},
  strengths: [],
  families: [],
  careers: [],
  challenges: [],
};

test('complete guest journey: ready reports, checkout, cumulative paid answers, protected publication', async () => {
  const guest = await begin();
  const free = await submit(guest, 'interests');
  assert.equal(free.statusCode, 200, free.body);
  const freeId = free.json().report_id;
  const pending = await app.inject({
    url: `/api/reports/${freeId}`,
    cookies: guest.cookies,
  });

  assert.equal(pending.json().status, 'ready');
  assert.equal(pending.json().payload.careers.length, 8);
  assert.equal(pending.json().display_name, 'Student');
  assert.equal(pending.json().format_version, 2);
  assert.ok(pending.json().generated_at);
  assert.equal((await submit(guest, 'strengths')).statusCode, 403);
  await buy(guest);
  const second = await submit(guest, 'strengths');
  assert.equal(second.statusCode, 200, second.body);
  const third = await submit(guest, 'eq');
  assert.equal(third.statusCode, 200, third.body);
  const records = await db.report.findMany({ orderBy: { stage: 'asc' } });
  assert.deepEqual(
    records.map((r) => (r.inputSnapshot as unknown[]).length),
    [32, 108, 138],
  );

  assert.equal(await db.answer.count(), 138);
  const reportId = third.json().report_id;
  const published = await app.inject({
    method: 'PUT',
    url: `/api/integrations/reports/${reportId}/result`,
    headers: { authorization: `Bearer ${secret}` },
    payload: {
      format_version: 1,
      payload,
    },
  });

  assert.equal(published.statusCode, 409, published.body);
  const read = await app.inject({
    url: `/api/reports/${reportId}`,
    cookies: guest.cookies,
  });

  assert.equal(read.json().payload.compass.top_matches.length, 5);
  assert.ok(read.json().payload.strength_profile);
  assert.ok(read.json().payload.eq_profile);
  const latest = await app.inject({
    url: `/api/responses/${guest.id}/report`,
    cookies: guest.cookies,
  });

  assert.equal(latest.json().id, reportId);
});

test('same email does not grant ownership, and paid sections cannot be skipped', async () => {
  const a = await begin();
  const b = await begin();
  const result = await submit(a, 'interests');
  assert.equal(
    (
      await app.inject({
        url: `/api/reports/${result.json().report_id}`,
        cookies: b.cookies,
      })
    ).statusCode,
    404,
  );

  assert.equal(
    (await app.inject({ url: `/api/questionnaire?response_id=${a.id}` })).statusCode,
    404,
  );

  assert.equal((await submit(b, 'eq')).statusCode, 409);
  assert.equal(
    (await db.attempt.findUniqueOrThrow({ where: { id: a.id } })).userId ===
      (await db.attempt.findUniqueOrThrow({ where: { id: b.id } })).userId,
    false,
  );
});

test('submissions are atomic and retry-safe under concurrency', async () => {
  const guest = await begin();
  const input = await answers(guest, 'interests');
  const invalid = await app.inject({
    method: 'POST',
    url: `/api/responses/${guest.id}/sections/interests`,
    cookies: guest.cookies,
    payload: { answers: input.slice(1) },
  });

  assert.equal(invalid.statusCode, 422);
  assert.equal(await db.answer.count(), 0);
  const results = await Promise.all([submit(guest, 'interests'), submit(guest, 'interests')]);
  assert.deepEqual(
    results.map((r) => r.statusCode),
    [200, 200],
  );

  assert.equal(results[0].json().report_id, results[1].json().report_id);
  assert.equal(await db.report.count(), 1);
  assert.equal(await db.answer.count(), 32);
  input.find((a) => 'numeric' in a)!.numeric = 5;
  const conflicting = await app.inject({
    method: 'POST',
    url: `/api/responses/${guest.id}/sections/interests`,
    cookies: guest.cookies,
    payload: { answers: input },
  });

  assert.equal(conflicting.statusCode, 409);
  await assert.rejects(db.answer.updateMany({ data: { text: 'changed' } }));
});

test('duplicate questions, foreign options, invalid scales and answer field combinations are rejected', async () => {
  const guest = await begin();
  const original = await answers(guest, 'interests');
  const cases = [
    [...original, original[0]],
    original.map((a, i) =>
      i === 0
        ? {
            ...a,
            option_ids: ['00000000-0000-4000-8000-000000000001'],
          }
        : a,
    ),
    original.map((a) =>
      'numeric' in a
        ? {
            ...a,
            numeric: 100,
          }
        : a,
    ),
    original.map((a, i) =>
      i === 0
        ? {
            ...a,
            text: 'unexpected',
          }
        : a,
    ),
  ];

  for (const input of cases) {
    const result = await app.inject({
      method: 'POST',
      url: `/api/responses/${guest.id}/sections/interests`,
      cookies: guest.cookies,
      payload: { answers: input },
    });

    assert.equal(result.statusCode, 422, result.body);
    assert.equal(await db.answer.count(), 0);
  }
});

test('payment grants are idempotent; refunds are reconciled before and after fulfillment', async () => {
  const guest = await begin();
  await submit(guest, 'interests');
  const checkout = await gateway.create(guest.id, 'ignored');
  await Promise.all([
    fulfill(db, gateway, checkout.session_id),
    fulfill(db, gateway, checkout.session_id),
  ]);

  assert.equal(await db.purchase.count(), 1);
  assert.equal(await db.entitlement.count(), 1);
  const state = checkouts.get(checkout.session_id)!;
  state.fullyRefunded = true;
  await reconcileRefund(db, gateway, state.paymentIntentId!);
  const status = await app.inject({
    url: `/api/checkout/${state.id}`,
    cookies: guest.cookies,
  });

  assert.equal(status.json().entitled, false);
  assert.equal(status.json().payment_status, 'refunded');
  assert.equal((await submit(guest, 'strengths')).statusCode, 403);
  const another = await begin();
  await submit(another, 'interests');
  const early = await gateway.create(another.id, 'ignored');
  checkouts.get(early.session_id)!.fullyRefunded = true;
  await reconcileRefund(db, gateway, checkouts.get(early.session_id)!.paymentIntentId!);
  await fulfill(db, gateway, early.session_id);
  assert.equal((await submit(another, 'strengths')).statusCode, 403);
});

test('unpaid checkout grants nothing and partial refunds retain access', async () => {
  const guest = await begin();
  await submit(guest, 'interests');
  const checkout = await gateway.create(guest.id, 'ignored');
  const state = checkouts.get(checkout.session_id)!;
  state.paid = false;
  await fulfill(db, gateway, state.id);
  assert.equal(await db.purchase.count(), 0);
  state.paid = true;
  await fulfill(db, gateway, state.id);
  await reconcileRefund(db, gateway, state.paymentIntentId!);
  assert.equal((await submit(guest, 'strengths')).statusCode, 200);
});

test('Google claim transfers access, not another verified account; logout clears browser access', async () => {
  const guest = await begin();
  await submit(guest, 'interests');
  await buy(guest);
  const user = await linkIdentity(
    db,
    {
      sub: 'verified-one',
      email: 'verified@example.com',
      name: 'Verified',
    },
    [guest.id],
  );

  assert.equal((await db.entitlement.findFirstOrThrow()).userId, user.id);
  const originalPayer = (await db.purchase.findFirstOrThrow()).userId;
  assert.notEqual(originalPayer, user.id);
  await linkIdentity(
    db,
    {
      sub: 'verified-two',
      email: 'other@example.com',
      name: 'Other',
    },
    [guest.id],
  );

  assert.equal((await db.attempt.findUniqueOrThrow({ where: { id: guest.id } })).userId, user.id);
  const cookie = app.encodeSecureSession(app.createSecureSession({ userId: user.id }));
  const me = await app.inject({
    url: '/api/auth/me',
    cookies: { fq_ts_session: cookie },
  });

  assert.equal(me.json().signed_in, true);
  const logout = await app.inject({
    method: 'POST',
    url: '/api/auth/logout',
    cookies: { fq_ts_session: cookie },
  });

  assert.equal(logout.statusCode, 204);
  assert.ok(logout.cookies.some((c) => c.value === ''));
  assert.equal(
    (await app.inject({ url: `/api/questionnaire?response_id=${guest.id}` })).statusCode,
    404,
  );
});

test('OAuth requires matching state and uses verified identity; CSRF origin is rejected', async () => {
  const bad = await app.inject({ url: '/api/auth/google/callback?code=x&state=x' });
  assert.equal(bad.statusCode, 302);
  assert.match(bad.headers.location!, /signin=failed/);
  const login = await app.inject({ url: '/api/auth/google/login' });
  const state = new URL(login.headers.location!).searchParams.get('state');
  const session = login.cookies.find((c) => c.name === 'fq_ts_session')!.value;
  const callback = await app.inject({
    url: `/api/auth/google/callback?code=valid&state=${state}`,
    cookies: { fq_ts_session: session },
  });

  assert.equal(callback.statusCode, 302);
  assert.equal(callback.headers.location, config.APP_ORIGIN);
  assert.equal(await db.user.count({ where: { googleSub: 'google-test' } }), 1);
  const csrf = await app.inject({
    method: 'POST',
    url: '/api/responses',
    headers: { origin: 'https://evil.example' },
    payload: {
      name: 'X',
      email: 'x@example.com',
    },
  });

  assert.equal(csrf.statusCode, 403);
});

test('publication requires service credentials, validates schema, and rejects conflicting writes', async () => {
  const guest = await begin();
  const submission = await submit(guest, 'interests');
  // Legacy pending reports can still receive service-published results.
  await db.report.update({
    where: { id: submission.json().report_id },
    data: {
      status: 'pending',
      payload: (await import('../src/generated/prisma/client.js')).Prisma.DbNull,
      generatedAt: null,
      formatVersion: 1,
      rulesetVersion: null,
    },
  });
  const url = `/api/integrations/reports/${submission.json().report_id}/result`;
  const body = {
    format_version: 1,
    payload,
  };

  assert.equal(
    (
      await app.inject({
        method: 'PUT',
        url,
        cookies: guest.cookies,
        payload: body,
      })
    ).statusCode,
    401,
  );

  const headers = { authorization: `Bearer ${secret}` };
  assert.equal(
    (
      await app.inject({
        method: 'PUT',
        url,
        headers,
        payload: {
          format_version: 1,
          payload: {},
        },
      })
    ).statusCode,
    422,
  );

  for (let i = 0; i < 2; i++) {
    assert.equal(
      (
        await app.inject({
          method: 'PUT',
          url,
          headers,
          payload: body,
        })
      ).statusCode,
      200,
    );
  }

  assert.equal(
    (
      await app.inject({
        method: 'PUT',
        url,
        headers,
        payload: {
          ...body,
          payload: {
            ...payload,
            challenges: [
              {
                title: 'Changed',
                detail: 'Changed',
              },
            ],
          },
        },
      })
    ).statusCode,
    409,
  );
});

test('disabled features return explicit errors without granting access, and seed is repeatable', async () => {
  const guest = await begin();
  const submission = await submit(guest, 'interests');
  assert.equal(
    (
      await app.inject({
        method: 'POST',
        url: `/api/responses/${guest.id}/apply_discount`,
        cookies: guest.cookies,
        payload: { discount_code: 'NOPE' },
      })
    ).statusCode,
    400,
  );

  assert.equal(
    (
      await app.inject({
        method: 'POST',
        url: `/api/responses/${guest.id}/checkout`,
        cookies: guest.cookies,
        payload: { discount_code: 'NOPE' },
      })
    ).statusCode,
    400,
  );

  assert.equal(
    (
      await app.inject({
        url: `/api/reports/${submission.json().report_id}/pdf`,
        cookies: guest.cookies,
      })
    ).statusCode,
    501,
  );

  assert.equal(await db.purchase.count(), 0);
  await seed(db);
  assert.equal(await db.question.count(), 138);

  const question = await db.question.findFirstOrThrow();
  await db.question.update({ where: { id: question.id }, data: { prompt: 'Changed wording' } });

  try {
    await assert.rejects(seed(db), /Question content differs and attempts exist/);
    assert.equal(await db.answer.count(), 32);
    assert.equal(await db.report.count(), 1);
  } finally {
    await db.question.update({ where: { id: question.id }, data: { prompt: question.prompt } });
  }
});

test('Stripe signature verification rejects missing and forged signatures', async () => {
  const realGateway = stripeGateway({
    ...config,
    STRIPE_SECRET_KEY: 'sk_test_fake',
    STRIPE_PRICE_ID: 'price_fake',
    STRIPE_WEBHOOK_SECRET: 'whsec_fake',
  });

  const webhookApp = await buildApp(config, db, { gateway: realGateway });

  try {
    assert.equal(
      (
        await webhookApp.inject({
          method: 'POST',
          url: '/api/stripe/webhook',
          payload: {},
        })
      ).statusCode,
      400,
    );

    assert.equal(
      (
        await webhookApp.inject({
          method: 'POST',
          url: '/api/stripe/webhook',
          headers: { 'stripe-signature': 'invalid' },
          payload: {},
        })
      ).statusCode,
      400,
    );
  } finally {
    await webhookApp.close();
  }
});

test('signed webhook fulfillment is durable without a browser return and accepts redelivery', async () => {
  const guest = await begin();
  await submit(guest, 'interests');
  const checkout = await gateway.create(guest.id, 'ignored');
  const realGateway = stripeGateway({
    ...config,
    STRIPE_SECRET_KEY: 'sk_test_fake',
    STRIPE_PRICE_ID: 'price_fake',
    STRIPE_WEBHOOK_SECRET: 'whsec_test',
  });

  const webhookApp = await buildApp(config, db, {
    gateway: {
      ...gateway,
      event: realGateway.event,
    },
  });

  const body = JSON.stringify({
    id: 'evt_completed',
    object: 'event',
    type: 'checkout.session.completed',
    data: { object: { id: checkout.session_id } },
  });

  const signature = new Stripe('sk_test_fake').webhooks.generateTestHeaderString({
    payload: body,
    secret: 'whsec_test',
  });

  try {
    for (let i = 0; i < 2; i++) {
      const result = await webhookApp.inject({
        method: 'POST',
        url: '/api/stripe/webhook',
        payload: body,
        headers: {
          'content-type': 'application/json',
          'stripe-signature': signature,
        },
      });

      assert.equal(result.statusCode, 200, result.body);
    }

    assert.equal(await db.purchase.count(), 1);
    assert.equal((await submit(guest, 'strengths')).statusCode, 200);
    const tampered = await webhookApp.inject({
      method: 'POST',
      url: '/api/stripe/webhook',
      payload: body + ' ',
      headers: {
        'content-type': 'application/json',
        'stripe-signature': signature,
      },
    });

    assert.equal(tampered.statusCode, 400);
  } finally {
    await webhookApp.close();
  }
});

test('paid forced choices enforce exact counts and bipolar answers enforce bounds', async () => {
  const guest = await begin();
  await submit(guest, 'interests');
  await buy(guest);
  const original = await answers(guest, 'strengths');
  const multiple = original.find(
    (answer) => 'option_ids' in answer && answer.option_ids.length > 1,
  )!;
  const bipolar = original.find((answer) => 'numeric' in answer && answer.numeric === -2)!;

  for (const invalid of [
    original.map((answer) =>
      answer === multiple && 'option_ids' in answer
        ? { ...answer, option_ids: answer.option_ids.slice(0, 1) }
        : answer,
    ),
    original.map((answer) => (answer === bipolar ? { ...answer, numeric: -3 } : answer)),
  ]) {
    const result = await app.inject({
      method: 'POST',
      url: `/api/responses/${guest.id}/sections/strengths`,
      cookies: guest.cookies,
      payload: { answers: invalid },
    });
    assert.equal(result.statusCode, 422, result.body);
    assert.equal(await db.answer.count(), 32);
  }
});

test('a missing scoring mapping rolls back the complete stage', async () => {
  const guest = await begin();
  const attempt = await db.attempt.findUniqueOrThrow({ where: { id: guest.id } });
  const snapshot = attempt.questionnaireSnapshot as unknown as { questions: Questions };
  snapshot.questions[0].reference_key = 'interests:unknown';
  await db.attempt.update({ where: { id: guest.id }, data: { questionnaireSnapshot: snapshot } });
  const result = await submit(guest, 'interests');
  assert.equal(result.statusCode, 500);
  assert.equal(await db.answer.count(), 0);
  assert.equal(await db.report.count(), 0);
  assert.equal(
    (await db.attempt.findUniqueOrThrow({ where: { id: guest.id } })).interestsCompletedAt,
    null,
  );
});

test('100% discount code grants access directly and enforces redemption cap', async () => {
  const code = await db.discountCode.create({
    data: { code: 'FREEPASS', maxRedemptions: 1 },
  });

  const guest = await begin();
  await submit(guest, 'interests');

  const applied = await app.inject({
    method: 'POST',
    url: `/api/responses/${guest.id}/apply_discount`,
    cookies: guest.cookies,
    payload: { discount_code: '  freepass  ' },
  });

  assert.equal(applied.statusCode, 200, applied.body);
  assert.equal(applied.json().entitled, true);

  const entitlement = await db.entitlement.findFirstOrThrow({
    where: { attemptId: guest.id },
  });
  assert.equal(entitlement.discountCodeId, code.id);
  assert.equal(entitlement.purchaseId, null);
  assert.equal((await db.discountCode.findUniqueOrThrow({ where: { id: code.id } })).redemptionsCount, 1);

  // Re-applying to an already-entitled attempt is rejected.
  const again = await app.inject({
    method: 'POST',
    url: `/api/responses/${guest.id}/apply_discount`,
    cookies: guest.cookies,
    payload: { discount_code: 'FREEPASS' },
  });
  assert.equal(again.statusCode, 409);

  // Cap is now exhausted for a second guest.
  const second = await begin();
  await submit(second, 'interests');
  const exhausted = await app.inject({
    method: 'POST',
    url: `/api/responses/${second.id}/apply_discount`,
    cookies: second.cookies,
    payload: { discount_code: 'FREEPASS' },
  });
  assert.equal(exhausted.statusCode, 400);
  assert.equal(await db.entitlement.count({ where: { attemptId: second.id } }), 0);
});

test('expired and not-yet-active codes are rejected', async () => {
  await db.discountCode.create({
    data: { code: 'OLD', endsAt: new Date(Date.now() - 60_000) },
  });
  await db.discountCode.create({
    data: { code: 'FUTURE', startsAt: new Date(Date.now() + 3_600_000) },
  });

  const guest = await begin();
  await submit(guest, 'interests');

  for (const discount_code of ['OLD', 'FUTURE']) {
    const result = await app.inject({
      method: 'POST',
      url: `/api/responses/${guest.id}/apply_discount`,
      cookies: guest.cookies,
      payload: { discount_code },
    });
    assert.equal(result.statusCode, 400);
  }
});

test('coupon-backed code applies at checkout and is counted once on fulfillment', async () => {
  const code = await db.discountCode.create({
    data: { code: 'SAVE10', stripeCouponId: 'coupon_save10' },
  });

  const guest = await begin();
  await submit(guest, 'interests');

  // Coupon codes cannot be applied without paying.
  const direct = await app.inject({
    method: 'POST',
    url: `/api/responses/${guest.id}/apply_discount`,
    cookies: guest.cookies,
    payload: { discount_code: 'SAVE10' },
  });
  assert.equal(direct.statusCode, 400);

  const checkout = await app.inject({
    method: 'POST',
    url: `/api/responses/${guest.id}/checkout`,
    cookies: guest.cookies,
    payload: { discount_code: 'SAVE10' },
  });
  assert.equal(checkout.statusCode, 200, checkout.body);

  const sessionId = checkout.json().session_id as string;
  assert.equal(checkouts.get(sessionId)?.discountCodeId, code.id);

  // Fulfill twice; redemption must be counted exactly once.
  await fulfill(db, gateway, sessionId);
  await fulfill(db, gateway, sessionId);

  const entitlement = await db.entitlement.findFirstOrThrow({
    where: { attemptId: guest.id },
  });
  assert.equal(entitlement.discountCodeId, code.id);
  assert.equal((await db.discountCode.findUniqueOrThrow({ where: { id: code.id } })).redemptionsCount, 1);
});

test('100% code cannot be used at checkout; coupon code cannot bypass payment', async () => {
  await db.discountCode.create({ data: { code: 'FREE100' } });

  const guest = await begin();
  await submit(guest, 'interests');

  const checkout = await app.inject({
    method: 'POST',
    url: `/api/responses/${guest.id}/checkout`,
    cookies: guest.cookies,
    payload: { discount_code: 'FREE100' },
  });
  assert.equal(checkout.statusCode, 400);
  assert.equal(await db.purchase.count(), 0);
});
