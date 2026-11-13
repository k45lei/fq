import { randomBytes, createHash } from 'node:crypto';
import { createRemoteJWKSet, jwtVerify } from 'jose';
import type { FastifyInstance, FastifyRequest } from 'fastify';
import type { Config } from './config.js';
import { type PrismaClient, type Tx, lockAttempt } from './db.js';
import { HttpError, uuid } from './domain.js';

declare module '@fastify/secure-session' {
  interface SessionData {
    userId: string; owned: string[];
    oauth: { state: string; nonce: string; verifier: string; expires: number };
  }
}
export const ownedIds = (request: FastifyRequest) => request.session.get('owned') ?? [];
export async function access(tx: Tx, request: FastifyRequest, id: string) {
  const attempt = await tx.attempt.findUnique({ where: { id } });
  const userId = request.session.get('userId');
  if (!attempt || !(attempt.userId === userId || ownedIds(request).includes(id))) {
    throw new HttpError(404, 'Attempt not found');
  }
  return attempt;
}
export async function entitled(tx: Tx, userId: string, attemptId: string) {
  return Boolean(await tx.entitlement.findFirst({ where: {
    userId, attemptId, revokedAt: null, validFrom: { lte: new Date() },
  }}));
}
export type GoogleIdentity = { sub: string; email: string; name: string };
const googleKeys = createRemoteJWKSet(new URL('https://www.googleapis.com/oauth2/v3/certs'));
export async function verifyGoogle(code: string, verifier: string, nonce: string, config: Config): Promise<GoogleIdentity> {
  const response = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST', signal: AbortSignal.timeout(15000),
    body: new URLSearchParams({ code, client_id: config.GOOGLE_CLIENT_ID, client_secret: config.GOOGLE_CLIENT_SECRET,
      redirect_uri: `${config.APP_ORIGIN}/api/auth/google/callback`, grant_type: 'authorization_code', code_verifier: verifier }),
  });
  if (!response.ok) throw new Error('Google token exchange failed');
  const token = await response.json() as { id_token?: string };
  if (!token.id_token) throw new Error('Missing identity token');
  const { payload } = await jwtVerify(token.id_token, googleKeys, {
    issuer: ['https://accounts.google.com', 'accounts.google.com'], audience: config.GOOGLE_CLIENT_ID,
  });
  if (payload.nonce !== nonce || payload.email_verified !== true || !payload.sub || typeof payload.email !== 'string') {
    throw new Error('Unverified Google identity');
  }
  return { sub: payload.sub, email: payload.email, name: typeof payload.name === 'string' ? payload.name : payload.email };
}
export async function linkIdentity(db: PrismaClient, identity: GoogleIdentity, owned: string[]) {
  return db.$transaction(async tx => {
    const user = await tx.user.upsert({ where: { googleSub: identity.sub },
      create: { name: identity.name, email: identity.email.toLowerCase(), googleSub: identity.sub },
      update: { email: identity.email.toLowerCase() },
    });
    // Match on verified provider subject, never on an unverified capture email.
    for (const id of [...new Set(owned)].sort()) {
      if (!uuid.safeParse(id).success) continue;
      await lockAttempt(tx, id);
      const attempt = await tx.attempt.findUnique({ where: { id }, include: { user: true } });
      if (!attempt || attempt.user.googleSub || attempt.userId === user.id) continue;
      await tx.attempt.update({ where: { id }, data: { userId: user.id } });
      await tx.entitlement.updateMany({ where: { attemptId: id }, data: { userId: user.id } });
      // Purchases keep the original payer; access follows the claimed attempt.
    }
    return user;
  });
}
export function authRoutes(app: FastifyInstance, db: PrismaClient, config: Config,
  verify: typeof verifyGoogle = verifyGoogle) {
  app.get('/api/auth/me', async request => {
    const id = request.session.get('userId');
    const user = id ? await db.user.findUnique({ where: { id } }) : null;
    if (!user) return { signed_in: false, reports: [] };
    const reports = await db.report.findMany({ where: { attempt: { userId: user.id } }, orderBy: [{ createdAt: 'desc' }, { stage: 'desc' }] });
    return { signed_in: true, name: user.name, email: user.email, reports: reports.map(r => ({
      id: r.id, tier: r.stage === 'interests' ? 'free' : 'full', status: r.status,
      created_at: r.createdAt, generated_at: r.generatedAt,
    })) };
  });
  app.post('/api/auth/logout', async (request, reply) => {
    request.session.delete();
    return reply.code(204).send();
  });
  app.get('/api/auth/google/login', async (request, reply) => {
    if (!config.GOOGLE_CLIENT_ID || !config.GOOGLE_CLIENT_SECRET) throw new HttpError(503, 'Google sign-in is unavailable');
    const state = randomBytes(32).toString('base64url'), nonce = randomBytes(32).toString('base64url');
    const verifier = randomBytes(32).toString('base64url');
    request.session.set('oauth', { state, nonce, verifier, expires: Date.now() + 600000 });
    const params = new URLSearchParams({ client_id: config.GOOGLE_CLIENT_ID,
      redirect_uri: `${config.APP_ORIGIN}/api/auth/google/callback`, response_type: 'code',
      scope: 'openid email profile', state, nonce, code_challenge: createHash('sha256').update(verifier).digest('base64url'), code_challenge_method: 'S256' });
    return reply.redirect(`https://accounts.google.com/o/oauth2/v2/auth?${params}`);
  });
  app.get('/api/auth/google/callback', async (request, reply) => {
    const oauth = request.session.get('oauth');
    request.session.set('oauth', undefined);
    const query = request.query as Record<string, string>;
    if (!oauth || oauth.expires < Date.now() || oauth.state !== query.state || !query.code) {
      return reply.redirect(`${config.APP_ORIGIN}/?signin=failed`);
    }
    try {
      const identity = await verify(query.code, oauth.verifier, oauth.nonce, config);
      const user = await linkIdentity(db, identity, ownedIds(request));
      request.session.regenerate();
      request.session.set('userId', user.id);
      return reply.redirect(config.APP_ORIGIN);
    } catch {
      request.log.warn('Google sign-in failed');
      return reply.redirect(`${config.APP_ORIGIN}/?signin=failed`);
    }
  });
}
