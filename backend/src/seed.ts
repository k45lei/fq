import { pathToFileURL } from 'node:url';
import { database, type PrismaClient } from './db.js';
import { canonical } from './domain.js';
import { instrumentRows, validateInstrument } from './scoring/instrument.js';

const DEFAULT_DISCOUNT_CODES = ['FREEPASS'];

const seedDiscountCodes = async (db: PrismaClient) => {
  for (const code of DEFAULT_DISCOUNT_CODES) {
    await db.discountCode.upsert({
      where: { code },
      update: {},
      create: { code },
    });
  }
};

export const seed = async (db: PrismaClient) => {
  validateInstrument();
  const rows = instrumentRows();

  await db.$transaction(async (tx) => {
    // Seeding is a maintenance operation, protected against concurrent starts/submissions.
    await tx.$executeRaw`LOCK TABLE attempts, questions, question_options IN EXCLUSIVE MODE`;
    const current = await tx.question.findMany({
      orderBy: { position: 'asc' },
      include: { options: { orderBy: { position: 'asc' } } },
    });

    const clean = current.map((q) => ({
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

  await seedDiscountCodes(db);
};

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (!process.env.DATABASE_URL) {
    throw new Error('DATABASE_URL is required');
  }

  const db = database(process.env.DATABASE_URL);

  try {
    await seed(db);
    console.info('Seeded the fixed 138-question instrument.');
  } finally {
    await db.$disconnect();
  }
}
