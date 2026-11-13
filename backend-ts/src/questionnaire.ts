import type { FastifyInstance } from 'fastify';
import type { PrismaClient, Prisma } from './db.js';
import { lockAttempt } from './db.js';
import { access, entitled, ownedIds } from './auth.js';
import {
  completed,
  completionColumns,
  hash,
  HttpError,
  stages,
  stageSchema,
  startInput,
  submissionInput,
  uuid,
} from './domain.js';

export const content = async (db: PrismaClient | Prisma.TransactionClient) => {
  const questions = await db.question.findMany({
    orderBy: { position: 'asc' },
    include: { options: { orderBy: { position: 'asc' } } },
  });

  if (!questions.length) {
    throw new HttpError(503, 'Questionnaire has not been seeded');
  }

  return questions.map(q => ({
    id: q.id,
    position: q.position,
    category: q.category,
    answer_type: q.answerType,
    prompt: q.prompt,
    help_text: q.helpText,
    scale_min: q.scaleMin,
    scale_max: q.scaleMax,
    max_selections: q.maxSelections,
    options: q.options.map(o => ({
      id: o.id,
      label: o.label,
    })),
  }));
};

export type Questions = Awaited<ReturnType<typeof content>>;

const metadata = {
  id: 'd353b452-9270-423a-8218-92bba0a72b6f',
  nickname: 'core-instrument',
  version: 'fixed',
};

const view = (questions: Questions, paid: boolean, done: readonly string[] = []) => {
  const sections = paid ? [...stages] : ['interests'];

  return {
    ...metadata,
    sections,
    completed_sections: done,
    questions: questions.filter(q => sections.includes(q.category)),
  };
};

export const questionnaireRoutes = (app: FastifyInstance, db: PrismaClient) => {
  app.get('/api/questionnaire', async request => {
    const { response_id } = request.query as { response_id?: string };

    if (!response_id) {
      return view(await content(db), false);
    }

    const attempt = await access(db, request, uuid.parse(response_id));
    const snapshot = attempt.questionnaireSnapshot as unknown as { questions: Questions };

    return view(
      snapshot.questions,
      await entitled(db, attempt.userId, attempt.id),
      completed(attempt),
    );
  });

  app.post('/api/responses', async (request, reply) => {
    const input = startInput.parse(request.body);
    const questions = await content(db);
    const signedIn = request.session.get('userId');
    const attempt = await db.$transaction(async tx => {
      const user = signedIn ? await tx.user.findUnique({ where: { id: signedIn } }) : null;

      if (signedIn && !user) {
        throw new HttpError(401, 'Please sign in again');
      }

      const owner =
        user ??
        (await tx.user.create({
          data: {
            name: input.name,
            email: input.email.toLowerCase(),
          },
        }));

      return tx.attempt.create({
        data: {
          userId: owner.id,
          questionnaireSnapshot: {
            ...metadata,
            questions,
          },
        },
      });
    });

    if (!signedIn) {
      request.session.set('owned', [attempt.id, ...ownedIds(request)].slice(0, 20));
    }

    return reply.code(201).send({
      response_id: attempt.id,
      questionnaire: view(questions, false),
    });
  });

  app.post('/api/responses/:responseId/sections/:section', async request => {
    const params = request.params as { responseId: string; section: string };
    const id = uuid.parse(params.responseId);
    const stage = stageSchema.parse(params.section);
    const input = submissionInput.parse(request.body);
    const normalized = input.answers
      .map(a => ({
        question_id: a.question_id,
        numeric: a.numeric ?? null,
        text: a.text?.trim() ?? null,
        option_ids: [...a.option_ids].sort(),
      }))
      .sort((a, b) => a.question_id.localeCompare(b.question_id));

    const submissionHash = hash(normalized);

    return db.$transaction(async tx => {
      await lockAttempt(tx, id);
      const attempt = await access(tx, request, id);
      const prior = await tx.report.findUnique({
        where: {
          attemptId_stage: {
            attemptId: id,
            stage,
          },
        },
      });

      if (prior) {
        if (prior.submissionHash !== submissionHash) {
          throw new HttpError(409, 'This section has already been submitted');
        }

        return { report_id: prior.id };
      }

      if (stages.slice(0, stages.indexOf(stage)).some(s => !attempt[completionColumns[s]])) {
        throw new HttpError(409, 'Complete the preceding sections first');
      }

      if (stage !== 'interests' && !(await entitled(tx, attempt.userId, id))) {
        throw new HttpError(403, 'Payment is required for this section');
      }

      const snapshot = attempt.questionnaireSnapshot as unknown as { questions: Questions };
      const questions = snapshot.questions.filter(q => q.category === stage);
      const submitted = new Map(normalized.map(a => [a.question_id, a]));

      if (
        submitted.size !== normalized.length ||
        questions.length !== submitted.size ||
        questions.some(q => !submitted.has(q.id))
      ) {
        throw new HttpError(422, 'Submit exactly one answer for every question in this section');
      }

      for (const question of questions) {
        const a = submitted.get(question.id)!;
        const options = new Set(a.option_ids);
        let valid = false;

        switch (question.answer_type) {
          case 'scale':
            valid =
              a.numeric !== null &&
              a.numeric >= question.scale_min! &&
              a.numeric <= question.scale_max! &&
              a.text === null &&
              !options.size;
            break;
          case 'free_text':
            valid = Boolean(a.text) && a.numeric === null && !options.size;
            break;
          case 'single_choice':
          case 'multi_choice':
            valid =
              a.numeric === null &&
              a.text === null &&
              options.size > 0 &&
              options.size === a.option_ids.length &&
              a.option_ids.every(o => question.options.some(v => v.id === o)) &&
              (question.answer_type !== 'single_choice' || options.size === 1) &&
              (question.max_selections === null || options.size <= question.max_selections);
        }

        if (!valid) {
          throw new HttpError(422, `Invalid answer for: ${question.prompt}`);
        }

        const saved = await tx.answer.create({
          data: {
            attemptId: id,
            questionId: question.id,
            numeric: a.numeric,
            text: a.text,
            snapshot: {
              category: stage,
              position: question.position,
              prompt: question.prompt,
              answer_type: question.answer_type,
              numeric: a.numeric,
              text: a.text,
              selected_options: question.options.filter(o => options.has(o.id)),
            },
          },
        });

        if (options.size) {
          await tx.answerChoice.createMany({
            data: a.option_ids.map(optionId => ({
              answerId: saved.id,
              questionId: question.id,
              optionId,
            })),
          });
        }
      }

      await tx.attempt.update({
        where: { id },
        data: { [completionColumns[stage]]: new Date() },
      });

      const answers = await tx.answer.findMany({
        where: { attemptId: id },
        include: { question: true },
        orderBy: { question: { position: 'asc' } },
      });

      const inputSnapshot = answers.map(a => a.snapshot);
      const report = await tx.report.create({
        data: {
          attemptId: id,
          stage,
          inputSnapshot: inputSnapshot as Prisma.InputJsonValue,
          inputHash: hash(inputSnapshot),
          submissionHash,
        },
      });

      return { report_id: report.id };
    });
  });
};
