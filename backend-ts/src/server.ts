import { buildApp } from './app.js';
import { configuration } from './config.js';
import { database } from './db.js';
const config = configuration();
const db = database(config.DATABASE_URL);
const app = await buildApp(config, db, { logger: true });
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, async () => {
  await app.close(); await db.$disconnect();
});
try { await app.listen({ port: config.PORT, host: '0.0.0.0' }); }
catch (error) { app.log.error(error); await db.$disconnect(); process.exitCode = 1; }
