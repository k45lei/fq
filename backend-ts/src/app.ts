import Fastify, { LogController } from 'fastify';
import secureSession from '@fastify/secure-session';
import cors from '@fastify/cors';
import rateLimit from '@fastify/rate-limit';
import { ZodError } from 'zod';
import type { Config } from './config.js';
import type { PrismaClient } from './db.js';
import { authRoutes, verifyGoogle } from './auth.js';
import { questionnaireRoutes } from './questionnaire.js';
import { reportRoutes } from './reports.js';
import { paymentRoutes, stripeGateway, type PaymentGateway } from './payments.js';
import { HttpError } from './domain.js';
export async function buildApp(config: Config, db: PrismaClient,
  options: { gateway?: PaymentGateway; verifyGoogle?: typeof verifyGoogle; logger?: boolean } = {}) {
  const app = Fastify({ bodyLimit: 512 * 1024, logger: options.logger ? {
    redact: ['req.headers.authorization', 'req.headers.cookie', 'res.headers["set-cookie"]'],
  } : false, logController: new LogController({ disableRequestLogging: true }) });
  await app.register(cors, { origin: config.APP_ORIGIN, credentials: true });
  await app.register(secureSession, { key: Buffer.from(config.SESSION_KEY, 'hex'),
    cookieName: 'fq_ts_session', expiry: 86400 * 7,
    cookie: { path: '/', httpOnly: true, sameSite: 'lax', secure: config.NODE_ENV === 'production' } });
  await app.register(rateLimit, { max: 180, timeWindow: '1 minute', allowList: request => request.url === '/api/stripe/webhook',
    skipOnError: false, global: config.NODE_ENV !== 'test' });
  app.addHook('onRequest', async request => {
    if (['GET', 'HEAD', 'OPTIONS'].includes(request.method) || request.url === '/api/stripe/webhook'
      || request.url.startsWith('/api/integrations/')) return;
    const origin = request.headers.origin;
    if ((origin && origin !== config.APP_ORIGIN) || request.headers['sec-fetch-site'] === 'cross-site') {
      throw new HttpError(403, 'Request origin is not allowed');
    }
  });
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof ZodError) return reply.code(422).send({ detail: error.issues[0]?.message ?? 'Invalid request' });
    const err = error as Error & { statusCode?: number; type?: string };
    if (err instanceof HttpError || (err.statusCode && err.statusCode < 500)) {
      return reply.code(err.statusCode!).send({ detail: err.message });
    }
    if (err.type?.startsWith('Stripe')) return reply.code(502).send({ detail: 'Payment provider is temporarily unavailable' });
    request.log.error({ name: err.name }, 'Request failed');
    return reply.code(500).send({ detail: 'An unexpected error occurred' });
  });
  app.get('/health', async () => { await db.$queryRaw`SELECT 1`; return { status: 'ok', environment: config.NODE_ENV }; });
  authRoutes(app, db, config, options.verifyGoogle);
  questionnaireRoutes(app, db);
  reportRoutes(app, db, config);
  paymentRoutes(app, db, options.gateway ?? stripeGateway(config));
  return app;
}
