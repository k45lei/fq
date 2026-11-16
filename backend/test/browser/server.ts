import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import EmbeddedPostgres from 'embedded-postgres';
import { buildApp } from '../../src/app.js';
import { configuration } from '../../src/config.js';
import { database } from '../../src/db.js';
import { seed } from '../../src/seed.js';
import type { Checkout } from '../../src/payments.js';

// This fixture never loads .env, connects to the application DB, or calls a provider.
const directory = await mkdtemp(join(tmpdir(), 'futurequest-browser-'));
const listener = createServer();
await new Promise<void>((resolve) => listener.listen(0, '127.0.0.1', resolve));
const databasePort = (listener.address() as { port: number }).port;
await new Promise<void>((resolve) => listener.close(() => resolve()));

const postgres = new EmbeddedPostgres({
  databaseDir: directory,
  port: databasePort,
  user: 'postgres',
  password: 'test-only',
  persistent: false,
  autoRehydrateSymlinks: true,
  onLog: () => {},
  onError: () => {},
});

await postgres.initialise();
await postgres.start();
await postgres.createDatabase('browser_test');

const databaseUrl = `postgresql://postgres:test-only@127.0.0.1:${databasePort}/browser_test`;
const db = database(databaseUrl);
const connection = postgres.getPgClient('browser_test');
await connection.connect();

try {
  const migrations = new URL('../../prisma/migrations/', import.meta.url);
  const entries = (await readdir(migrations, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory())
    .sort((first, second) => first.name.localeCompare(second.name));

  for (const entry of entries) {
    await connection.query(
      await readFile(new URL(`${entry.name}/migration.sql`, migrations), 'utf8'),
    );
  }
} finally {
  await connection.end();
}

await seed(db);

const origin = 'http://127.0.0.1:4175';
const checkouts = new Map<string, Checkout>();
const config = configuration({
  DATABASE_URL: databaseUrl,
  SESSION_KEY: 'ab'.repeat(32),
  NODE_ENV: 'test',
  APP_ORIGIN: origin,
  GOOGLE_CLIENT_ID: 'test',
  GOOGLE_CLIENT_SECRET: 'test',
});

const app = await buildApp(config, db, {
  verifyGoogle: async () => ({
    sub: 'browser-test',
    name: 'Browser Student',
    email: 'browser@example.com',
  }),
  gateway: {
    price: async () => ({ amount_cents: 1995, currency: 'cad' }),
    create: async (responseId) => {
      const id = `cs_${responseId}`;
      checkouts.set(id, {
        id,
        responseId,
        priceId: 'price_test',
        paymentIntentId: `pi_${responseId}`,
        paid: false,
        fullyRefunded: false,
        amount: 1995,
        currency: 'cad',
      });

      return { session_id: id, url: `${origin}/api/test/checkout/${id}` };
    },
    retrieve: async (id) => {
      const checkout = checkouts.get(id);

      if (!checkout) {
        throw new Error('Unknown test checkout');
      }

      return checkout;
    },
    event: () => {
      throw new Error('Use integration tests for webhook verification');
    },
    refunded: async () => false,
  },
});

app.get<{ Params: { id: string } }>('/api/test/checkout/:id', async (request, reply) => {
  const checkout = checkouts.get(request.params.id);

  if (!checkout) {
    return reply.code(404).send();
  }

  return reply.type('text/html').send(`
    <h1>Test checkout</h1>
    <a href="/api/test/pay/${checkout.id}">Pay</a>
    <a href="/checkout/cancelled?response_id=${checkout.responseId}">Cancel</a>
  `);
});

app.get<{ Params: { id: string } }>('/api/test/pay/:id', async (request, reply) => {
  const checkout = checkouts.get(request.params.id);

  if (!checkout) {
    return reply.code(404).send();
  }

  checkout.paid = true;

  return reply.redirect(`${origin}/checkout/success?session_id=${checkout.id}`);
});

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.once(signal, async () => {
    await app.close();
    await db.$disconnect();
    await postgres.stop();
    await rm(directory, { recursive: true, force: true });
  });
}

await app.listen({ host: '127.0.0.1', port: 8001 });
console.info('Browser fixture ready at http://127.0.0.1:8001');
