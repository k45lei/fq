import { configuration } from './config.js';
import { database } from './db.js';

const config = configuration();
const db = database(config.DATABASE_URL);

await db.$queryRaw`SELECT 1`;

console.info('Worker ready. Reports are calculated synchronously; no background jobs are enabled.');

// Intentionally idle: deterministic scoring runs inside stage-submission transactions.
const keepAlive = setInterval(() => {}, 60000);

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.once(signal, async () => {
    clearInterval(keepAlive);
    await db.$disconnect();
  });
}
