import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { parse } from 'yaml';
import { z } from 'zod';
import { database, type PrismaClient } from './db.js';
import { canonical, stageSchema } from './domain.js';

const spec = z.object({
  questions: z
    .array(
      z
        .object({
          category: stageSchema,
          type: z.enum(['single_choice', 'multi_choice', 'scale', 'free_text']),
          prompt: z.string().min(1),
          help_text: z.string().optional(),
          scale_min: z.number().int().optional(),
          scale_max: z.number().int().optional(),
          max_selections: z.number().int().positive().optional(),
          options: z.array(z.object({ label: z.string().min(1) })).default([]),
        })
        .superRefine((q, ctx) => {
          if (
            q.type === 'scale' &&
            !(q.scale_min !== undefined && q.scale_max !== undefined && q.scale_min < q.scale_max)
          ) {
            ctx.addIssue({
              code: 'custom',
              message: 'Invalid scale bounds',
            });
          }

          if (['single_choice', 'multi_choice'].includes(q.type) !== (q.options.length > 0)) {
            ctx.addIssue({
              code: 'custom',
              message: 'Choice questions require options; other types forbid them',
            });
          }
        }),
    )
    .min(3),
});

const stableId = (key: string) => {
  const hex = createHash('sha256').update(`futurequest-fixed:${key}`).digest('hex');

  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
};

export const seed = async (db: PrismaClient) => {
  const input = spec.parse(
    parse(
      await readFile(
        new URL('../../backend/seeds/core-instrument-v2.yaml', import.meta.url),
        'utf8',
      ),
    ),
  );

  const rows = input.questions.map((q, i) => ({
    id: stableId(`q:${i + 1}`),
    position: i + 1,
    category: q.category,
    answerType: q.type,
    prompt: q.prompt,
    helpText: q.help_text ?? null,
    scaleMin: q.scale_min ?? null,
    scaleMax: q.scale_max ?? null,
    maxSelections: q.max_selections ?? null,
    options: q.options.map((o, j) => ({
      id: stableId(`q:${i + 1}:o:${j + 1}`),
      position: j + 1,
      label: o.label,
    })),
  }));

  await db.$transaction(async tx => {
    // Seeding is a maintenance operation, protected against concurrent starts/submissions.
    await tx.$executeRaw`LOCK TABLE attempts, questions, question_options IN EXCLUSIVE MODE`;
    const current = await tx.question.findMany({
      orderBy: { position: 'asc' },
      include: { options: { orderBy: { position: 'asc' } } },
    });

    const clean = current.map(q => ({
      ...q,
      options: q.options.map(({ questionId: _id, ...o }) => o),
    }));

    if (canonical(clean) === canonical(rows)) {
      return;
    }

    if (await tx.attempt.count()) {
      throw new Error(
        'Question content differs and attempts exist. Seed only a fresh database or unchanged content.',
      );
    }

    await tx.questionOption.deleteMany();
    await tx.question.deleteMany();

    for (const { options, ...question } of rows) {
      await tx.question.create({
        data: {
          ...question,
          options: { create: options },
        },
      });
    }
  });
};

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (!process.env.DATABASE_URL) {
    throw new Error('DATABASE_URL is required');
  }

  const db = database(process.env.DATABASE_URL);

  try {
    await seed(db);
    console.info('Seeded the fixed 40-question instrument.');
  } finally {
    await db.$disconnect();
  }
}
