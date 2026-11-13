import assert from 'node:assert/strict';
import { after, before, beforeEach, test } from 'node:test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
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
import { fulfill, reconcileRefund, stripeGateway, type Checkout, type PaymentGateway } from '../src/payments.js';
import type { Questions } from '../src/questionnaire.js';
import type { Stage } from '../src/domain.js';

let pg: EmbeddedPostgres, db: PrismaClient, app: Awaited<ReturnType<typeof buildApp>>, dir: string;
const checkouts = new Map<string, Checkout>();
const gateway: PaymentGateway = {
  price: async () => ({ amount_cents: 2999, currency: 'usd' }),
  create: async (id) => {
    const session = `cs_${id}`;
    checkouts.set(session, { id: session, responseId: id, priceId: 'price_original', paymentIntentId: `pi_${id}`,
      amount: 2999, currency: 'usd', paid: true, fullyRefunded: false });
    return { session_id: session, url: `https://checkout.stripe.com/${session}` };
  },
  retrieve: async id => { const result = checkouts.get(id); assert.ok(result, `Unknown checkout ${id}`); return { ...result }; },
  event: () => { throw new Error('Use real signature verification in webhook test'); },
  refunded: async intent => [...checkouts.values()].some(c => c.paymentIntentId === intent && c.fullyRefunded),
};
const secret = 'integration-secret-'.repeat(3);
let config: ReturnType<typeof configuration>;
before(async () => {
  // Allocate a disposable real PostgreSQL cluster, never read an ambient DATABASE_URL.
  dir = await mkdtemp(join(tmpdir(), 'futurequest-ts-test-'));
  const listener = createServer();
  await new Promise<void>(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = (listener.address() as { port: number }).port;
  await new Promise<void>(resolve => listener.close(() => resolve()));
  pg = new EmbeddedPostgres({ databaseDir: dir, port, user: 'postgres', password: 'test-only',
    persistent: false, autoRehydrateSymlinks: true, onLog: () => {}, onError: () => {} });
  await pg.initialise(); await pg.start(); await pg.createDatabase('futurequest_test');
  const url = `postgresql://postgres:test-only@127.0.0.1:${port}/futurequest_test`;
  const connection = pg.getPgClient('futurequest_test');
  await connection.connect();
  try { await connection.query(await readFile(new URL('../prisma/migrations/202611110001_initial/migration.sql', import.meta.url), 'utf8')); }
  finally { await connection.end(); }
  db = database(url);
  await seed(db);
  config = configuration({ DATABASE_URL: url, SESSION_KEY: 'ab'.repeat(32), NODE_ENV: 'test',
    RESULT_API_SECRET: secret, GOOGLE_CLIENT_ID: 'test', GOOGLE_CLIENT_SECRET: 'test' });
  app = await buildApp(config, db, { gateway, verifyGoogle: async () => ({ sub: 'google-test', email: 'verified@example.com', name: 'Verified' }) });
  await app.ready();
});
after(async () => {
  if (app) await app.close();
  if (db) await db.$disconnect();
  if (pg) await pg.stop();
  if (dir) await rm(dir, { recursive: true, force: true });
});
beforeEach(async () => {
  checkouts.clear();
  await db.$executeRawUnsafe('TRUNCATE users, attempts, answers, answer_choices, reports, purchases, entitlements RESTART IDENTITY CASCADE');
});
async function begin() {
  const result = await app.inject({ method: 'POST', url: '/api/responses', payload: { name: 'Student', email: 'student@example.com' } });
  assert.equal(result.statusCode, 201, result.body);
  const cookie = result.cookies.find(c => c.name === 'fq_ts_session')!;
  return { id: result.json().response_id as string, cookies: { fq_ts_session: cookie.value } };
}
type Guest = Awaited<ReturnType<typeof begin>>;
async function answers(guest: Guest, stage: Stage) {
  const attempt = await db.attempt.findUniqueOrThrow({ where: { id: guest.id } });
  const qs = (attempt.questionnaireSnapshot as unknown as { questions: Questions }).questions;
  return qs.filter(q => q.category === stage).map(q => ({ question_id: q.id,
    ...(q.answer_type === 'scale' ? { numeric: q.scale_min! } : q.answer_type === 'free_text' ? { text: 'I enjoy learning new things.' } : { option_ids: [q.options[0].id] }),
  }));
}
async function submit(guest: Guest, stage: Stage) {
  return app.inject({ method: 'POST', url: `/api/responses/${guest.id}/sections/${stage}`, cookies: guest.cookies,
    payload: { answers: await answers(guest, stage) } });
}
async function buy(guest: Guest) {
  const checkout = await app.inject({ method: 'POST', url: `/api/responses/${guest.id}/checkout`, cookies: guest.cookies, payload: {} });
  assert.equal(checkout.statusCode, 200, checkout.body);
  const status = await app.inject({ url: `/api/checkout/${checkout.json().session_id}`, cookies: guest.cookies });
  assert.equal(status.statusCode, 200, status.body); assert.equal(status.json().entitled, true);
  return checkout.json().session_id as string;
}
const payload = { holland_code: [], traits: {}, strengths: [], families: [], careers: [], challenges: [] };

test('complete guest journey: free placeholder, checkout, cumulative paid answers, protected publication', async () => {
  const guest = await begin();
  const free = await submit(guest, 'interests'); assert.equal(free.statusCode, 200, free.body);
  const freeId = free.json().report_id;
  const pending = await app.inject({ url: `/api/reports/${freeId}`, cookies: guest.cookies });
  assert.equal(pending.json().status, 'pending'); assert.equal(pending.json().payload, null);
  assert.equal(pending.json().generated_at, null);
  assert.equal((await submit(guest, 'strengths')).statusCode, 403);
  await buy(guest);
  const second = await submit(guest, 'strengths'); assert.equal(second.statusCode, 200, second.body);
  const third = await submit(guest, 'eq'); assert.equal(third.statusCode, 200, third.body);
  const records = await db.report.findMany({ orderBy: { stage: 'asc' } });
  assert.deepEqual(records.map(r => (r.inputSnapshot as unknown[]).length), [14, 28, 40]);
  assert.equal(await db.answer.count(), 40);
  const reportId = third.json().report_id;
  const published = await app.inject({ method: 'PUT', url: `/api/integrations/reports/${reportId}/result`,
    headers: { authorization: `Bearer ${secret}` }, payload: { format_version: 1, payload } });
  assert.equal(published.statusCode, 200, published.body);
  assert.equal(published.json().status, 'ready'); assert.ok(published.json().generated_at);
  const read = await app.inject({ url: `/api/reports/${reportId}`, cookies: guest.cookies });
  assert.deepEqual(read.json().payload, payload);
  const latest = await app.inject({ url: `/api/responses/${guest.id}/report`, cookies: guest.cookies });
  assert.equal(latest.json().id, reportId);
});

test('same email does not grant ownership, and paid sections cannot be skipped', async () => {
  const a = await begin(), b = await begin();
  const result = await submit(a, 'interests');
  assert.equal((await app.inject({ url: `/api/reports/${result.json().report_id}`, cookies: b.cookies })).statusCode, 404);
  assert.equal((await app.inject({ url: `/api/questionnaire?response_id=${a.id}` })).statusCode, 404);
  assert.equal((await submit(b, 'eq')).statusCode, 409);
  assert.equal((await db.attempt.findUniqueOrThrow({ where: { id: a.id } })).userId ===
    (await db.attempt.findUniqueOrThrow({ where: { id: b.id } })).userId, false);
});

test('submissions are atomic and retry-safe under concurrency', async () => {
  const guest = await begin();
  const input = await answers(guest, 'interests');
  const invalid = await app.inject({ method: 'POST', url: `/api/responses/${guest.id}/sections/interests`, cookies: guest.cookies,
    payload: { answers: input.slice(1) } });
  assert.equal(invalid.statusCode, 422); assert.equal(await db.answer.count(), 0);
  const results = await Promise.all([submit(guest, 'interests'), submit(guest, 'interests')]);
  assert.deepEqual(results.map(r => r.statusCode), [200, 200]);
  assert.equal(results[0].json().report_id, results[1].json().report_id);
  assert.equal(await db.report.count(), 1); assert.equal(await db.answer.count(), 14);
  input.find(a => 'numeric' in a)!.numeric = 5;
  const conflicting = await app.inject({ method: 'POST', url: `/api/responses/${guest.id}/sections/interests`, cookies: guest.cookies, payload: { answers: input } });
  assert.equal(conflicting.statusCode, 409);
  await assert.rejects(db.answer.updateMany({ data: { text: 'changed' } }));
});

test('duplicate questions, foreign options, invalid scales and answer field combinations are rejected', async () => {
  const guest = await begin();
  const original = await answers(guest, 'interests');
  const cases = [
    [...original, original[0]],
    original.map((a, i) => i === 0 ? { ...a, option_ids: ['00000000-0000-4000-8000-000000000001'] } : a),
    original.map(a => 'numeric' in a ? { ...a, numeric: 100 } : a),
    original.map((a, i) => i === 0 ? { ...a, text: 'unexpected' } : a),
  ];
  for (const input of cases) {
    const result = await app.inject({ method: 'POST', url: `/api/responses/${guest.id}/sections/interests`, cookies: guest.cookies, payload: { answers: input } });
    assert.equal(result.statusCode, 422, result.body); assert.equal(await db.answer.count(), 0);
  }
});

test('payment grants are idempotent; refunds are reconciled before and after fulfillment', async () => {
  const guest = await begin(); await submit(guest, 'interests');
  const checkout = await gateway.create(guest.id, 'ignored');
  await Promise.all([fulfill(db, gateway, checkout.session_id), fulfill(db, gateway, checkout.session_id)]);
  assert.equal(await db.purchase.count(), 1); assert.equal(await db.entitlement.count(), 1);
  const state = checkouts.get(checkout.session_id)!;
  state.fullyRefunded = true;
  await reconcileRefund(db, gateway, state.paymentIntentId!);
  const status = await app.inject({ url: `/api/checkout/${state.id}`, cookies: guest.cookies });
  assert.equal(status.json().entitled, false); assert.equal(status.json().payment_status, 'refunded');
  assert.equal((await submit(guest, 'strengths')).statusCode, 403);
  const another = await begin(); await submit(another, 'interests');
  const early = await gateway.create(another.id, 'ignored');
  checkouts.get(early.session_id)!.fullyRefunded = true;
  await reconcileRefund(db, gateway, checkouts.get(early.session_id)!.paymentIntentId!);
  await fulfill(db, gateway, early.session_id);
  assert.equal((await submit(another, 'strengths')).statusCode, 403);
});

test('unpaid checkout grants nothing and partial refunds retain access', async () => {
  const guest = await begin(); await submit(guest, 'interests');
  const checkout = await gateway.create(guest.id, 'ignored');
  const state = checkouts.get(checkout.session_id)!; state.paid = false;
  await fulfill(db, gateway, state.id); assert.equal(await db.purchase.count(), 0);
  state.paid = true; await fulfill(db, gateway, state.id);
  await reconcileRefund(db, gateway, state.paymentIntentId!);
  assert.equal((await submit(guest, 'strengths')).statusCode, 200);
});

test('Google claim transfers access, not another verified account; logout clears browser access', async () => {
  const guest = await begin(); await submit(guest, 'interests'); await buy(guest);
  const user = await linkIdentity(db, { sub: 'verified-one', email: 'verified@example.com', name: 'Verified' }, [guest.id]);
  assert.equal((await db.entitlement.findFirstOrThrow()).userId, user.id);
  const originalPayer = (await db.purchase.findFirstOrThrow()).userId;
  assert.notEqual(originalPayer, user.id);
  await linkIdentity(db, { sub: 'verified-two', email: 'other@example.com', name: 'Other' }, [guest.id]);
  assert.equal((await db.attempt.findUniqueOrThrow({ where: { id: guest.id } })).userId, user.id);
  const cookie = app.encodeSecureSession(app.createSecureSession({ userId: user.id }));
  const me = await app.inject({ url: '/api/auth/me', cookies: { fq_ts_session: cookie } });
  assert.equal(me.json().signed_in, true);
  const logout = await app.inject({ method: 'POST', url: '/api/auth/logout', cookies: { fq_ts_session: cookie } });
  assert.equal(logout.statusCode, 204); assert.ok(logout.cookies.some(c => c.value === ''));
  assert.equal((await app.inject({ url: `/api/questionnaire?response_id=${guest.id}` })).statusCode, 404);
});

test('OAuth requires matching state and uses verified identity; CSRF origin is rejected', async () => {
  const bad = await app.inject({ url: '/api/auth/google/callback?code=x&state=x' });
  assert.equal(bad.statusCode, 302); assert.match(bad.headers.location!, /signin=failed/);
  const login = await app.inject({ url: '/api/auth/google/login' });
  const state = new URL(login.headers.location!).searchParams.get('state');
  const session = login.cookies.find(c => c.name === 'fq_ts_session')!.value;
  const callback = await app.inject({ url: `/api/auth/google/callback?code=valid&state=${state}`, cookies: { fq_ts_session: session } });
  assert.equal(callback.statusCode, 302); assert.equal(callback.headers.location, config.APP_ORIGIN);
  assert.equal(await db.user.count({ where: { googleSub: 'google-test' } }), 1);
  const csrf = await app.inject({ method: 'POST', url: '/api/responses', headers: { origin: 'https://evil.example' }, payload: { name: 'X', email: 'x@example.com' } });
  assert.equal(csrf.statusCode, 403);
});

test('publication requires service credentials, validates schema, and rejects conflicting writes', async () => {
  const guest = await begin(), submission = await submit(guest, 'interests');
  const url = `/api/integrations/reports/${submission.json().report_id}/result`;
  const body = { format_version: 1, payload };
  assert.equal((await app.inject({ method: 'PUT', url, cookies: guest.cookies, payload: body })).statusCode, 401);
  const headers = { authorization: `Bearer ${secret}` };
  assert.equal((await app.inject({ method: 'PUT', url, headers, payload: { format_version: 1, payload: {} } })).statusCode, 422);
  for (let i = 0; i < 2; i++) assert.equal((await app.inject({ method: 'PUT', url, headers, payload: body })).statusCode, 200);
  assert.equal((await app.inject({ method: 'PUT', url, headers, payload: { ...body, payload: { ...payload, challenges: [{ title: 'Changed', detail: 'Changed' }] } } })).statusCode, 409);
});

test('disabled features return explicit errors without granting access, and seed is repeatable', async () => {
  const guest = await begin(), submission = await submit(guest, 'interests');
  assert.equal((await app.inject({ method: 'POST', url: `/api/responses/${guest.id}/apply_discount`, cookies: guest.cookies, payload: { discount_code: 'ANY' } })).statusCode, 501);
  assert.equal((await app.inject({ method: 'POST', url: `/api/responses/${guest.id}/checkout`, cookies: guest.cookies, payload: { discount_code: 'ANY' } })).statusCode, 501);
  assert.equal((await app.inject({ url: `/api/reports/${submission.json().report_id}/pdf`, cookies: guest.cookies })).statusCode, 501);
  assert.equal(await db.purchase.count(), 0);
  await seed(db); assert.equal(await db.question.count(), 40);
});

test('Stripe signature verification rejects missing and forged signatures', async () => {
  const realGateway = stripeGateway({ ...config, STRIPE_SECRET_KEY: 'sk_test_fake', STRIPE_PRICE_ID: 'price_fake', STRIPE_WEBHOOK_SECRET: 'whsec_fake' });
  const webhookApp = await buildApp(config, db, { gateway: realGateway });
  try {
    assert.equal((await webhookApp.inject({ method: 'POST', url: '/api/stripe/webhook', payload: {} })).statusCode, 400);
    assert.equal((await webhookApp.inject({ method: 'POST', url: '/api/stripe/webhook', headers: { 'stripe-signature': 'invalid' }, payload: {} })).statusCode, 400);
  } finally { await webhookApp.close(); }
});

test('signed webhook fulfillment is durable without a browser return and accepts redelivery', async () => {
  const guest = await begin(); await submit(guest, 'interests');
  const checkout = await gateway.create(guest.id, 'ignored');
  const realGateway = stripeGateway({ ...config, STRIPE_SECRET_KEY: 'sk_test_fake', STRIPE_PRICE_ID: 'price_fake', STRIPE_WEBHOOK_SECRET: 'whsec_test' });
  const webhookApp = await buildApp(config, db, { gateway: { ...gateway, event: realGateway.event } });
  const body = JSON.stringify({ id: 'evt_completed', object: 'event', type: 'checkout.session.completed', data: { object: { id: checkout.session_id } } });
  const signature = new Stripe('sk_test_fake').webhooks.generateTestHeaderString({ payload: body, secret: 'whsec_test' });
  try {
    for (let i = 0; i < 2; i++) {
      const result = await webhookApp.inject({ method: 'POST', url: '/api/stripe/webhook', payload: body,
        headers: { 'content-type': 'application/json', 'stripe-signature': signature } });
      assert.equal(result.statusCode, 200, result.body);
    }
    assert.equal(await db.purchase.count(), 1);
    assert.equal((await submit(guest, 'strengths')).statusCode, 200);
    const tampered = await webhookApp.inject({ method: 'POST', url: '/api/stripe/webhook', payload: body + ' ',
      headers: { 'content-type': 'application/json', 'stripe-signature': signature } });
    assert.equal(tampered.statusCode, 400);
  } finally { await webhookApp.close(); }
});
