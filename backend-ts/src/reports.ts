import { timingSafeEqual } from 'node:crypto';
import type { FastifyInstance } from 'fastify';
import type { Config } from './config.js';
import type { PrismaClient } from './db.js';
import type { Report } from './generated/prisma/client.js';
import { access } from './auth.js';
import { canonical, HttpError, resultInput, uuid } from './domain.js';

export const reportOut = (report: Report) => {
  return {
    id: report.id,
    response_id: report.attemptId,
    stage: report.stage,
    tier: report.stage === 'interests' ? 'free' : 'full',
    status: report.status,
    created_at: report.createdAt,
    generated_at: report.generatedAt,
    payload: report.payload,
  };
};

// Provider-neutral publication: no transport, model or student identity is trusted here.
export const publishResult = async (db: PrismaClient, id: string, value: unknown) => {
  const input = resultInput.parse(value);

  return db.$transaction(async tx => {
    await tx.$queryRaw`SELECT id FROM reports WHERE id = ${id}::uuid FOR UPDATE`;
    const existing = await tx.report.findUnique({ where: { id } });

    if (!existing) {
      throw new HttpError(404, 'Report not found');
    }

    if (existing.status === 'ready') {
      if (
        canonical(existing.payload) !== canonical(input.payload) ||
        existing.formatVersion !== input.format_version
      ) {
        throw new HttpError(409, 'A different result has already been published');
      }

      return existing;
    }

    return tx.report.update({
      where: { id },
      data: {
        status: 'ready',
        payload: input.payload,
        formatVersion: input.format_version,
        generatedAt: new Date(),
      },
    });
  });
};

export const reportRoutes = (app: FastifyInstance, db: PrismaClient, config: Config) => {
  app.get('/api/reports/:reportId', async request => {
    const id = uuid.parse((request.params as { reportId: string }).reportId);
    const report = await db.report.findUnique({ where: { id } });

    if (!report) {
      throw new HttpError(404, 'Report not found');
    }

    try {
      await access(db, request, report.attemptId);
    } catch {
      throw new HttpError(404, 'Report not found');
    }

    return reportOut(report);
  });

  app.get('/api/responses/:responseId/report', async request => {
    const id = uuid.parse((request.params as { responseId: string }).responseId);
    await access(db, request, id);
    const report = await db.report.findFirst({
      where: { attemptId: id },
      orderBy: { stage: 'desc' },
    });

    if (!report) {
      throw new HttpError(404, 'Report not found');
    }

    return reportOut(report);
  });

  app.get('/api/reports/:reportId/pdf', async request => {
    const id = uuid.parse((request.params as { reportId: string }).reportId);
    const report = await db.report.findUnique({ where: { id } });

    if (!report) {
      throw new HttpError(404, 'Report not found');
    }

    try {
      await access(db, request, report.attemptId);
    } catch {
      throw new HttpError(404, 'Report not found');
    }

    throw new HttpError(501, 'PDF downloads are not available yet');
  });

  app.put('/api/integrations/reports/:reportId/result', async request => {
    if (!config.RESULT_API_SECRET) {
      throw new HttpError(503, 'Result integration is disabled');
    }

    const expected = Buffer.from(`Bearer ${config.RESULT_API_SECRET}`);
    const actual = Buffer.from(request.headers.authorization ?? '');

    if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
      throw new HttpError(401, 'Invalid integration credentials');
    }

    const id = uuid.parse((request.params as { reportId: string }).reportId);

    return reportOut(await publishResult(db, id, request.body));
  });
};
