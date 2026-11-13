import { configuration } from './config.js';
import { database } from './db.js';
const config = configuration();
const db = database(config.DATABASE_URL);
await db.$queryRaw`SELECT 1`;
console.info('Worker ready. No background jobs are enabled in the storage-only phase.');
// Intentionally idle: result publication is a transactional API operation. Introduce a
// durable Postgres queue when actual background work is added, never an in-memory queue.
const keepAlive = setInterval(() => {}, 60000);
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, async () => {
  clearInterval(keepAlive); await db.$disconnect();
});
