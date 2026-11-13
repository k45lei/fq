import { createHash } from 'node:crypto';
import { z } from 'zod';
import type { Attempt } from './generated/prisma/client.js';

export const stages = ['interests', 'strengths', 'eq'] as const;
export type Stage = (typeof stages)[number];
export const stageSchema = z.enum(stages);
export const uuid = z.uuid();

export const completionColumns = {
  interests: 'interestsCompletedAt',
  strengths: 'strengthsCompletedAt',
  eq: 'eqCompletedAt',
} as const;

export const completed = (attempt: Attempt) => {
  return stages.filter(stage => attempt[completionColumns[stage]] !== null);
};

export class HttpError extends Error {
  constructor(
    public statusCode: number,
    message: string,
  ) {
    super(message);
  }
}

export const canonical = (value: unknown): string => {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(',')}]`;
  }

  if (value && typeof value === 'object') {
    const object = value as Record<string, unknown>;

    return `{${Object.keys(object)
      .sort()
      .map(k => `${JSON.stringify(k)}:${canonical(object[k])}`)
      .join(',')}}`;
  }

  return JSON.stringify(value);
};

export const hash = (value: unknown) => createHash('sha256').update(canonical(value)).digest('hex');

export const answerInput = z
  .object({
    question_id: uuid,
    numeric: z.number().int().optional(),
    text: z.string().max(10000).optional(),
    option_ids: z.array(uuid).max(100).default([]),
  })
  .strict();

export const submissionInput = z.object({ answers: z.array(answerInput).min(1).max(100) }).strict();

export const startInput = z
  .object({
    name: z.string().trim().min(1).max(200),
    email: z.email().max(320),
    school: z.string().max(300).nullable().optional(), // Accepted for frontend compatibility only.
  })
  .strict();

const score = z.number().min(0).max(1);
const prose = z.string().max(10000);

const pay = z
  .object({
    low: z.number().nonnegative().nullable(),
    median: z.number().nonnegative().nullable(),
    high: z.number().nonnegative().nullable(),
    currency: z.string().length(3),
    source: z.enum(['bls_oews', 'model_research', 'mock']),
    source_url: z.url().nullable(),
    as_of: z.iso.date(),
  })
  .strict();

// Explicit output contract; JSON is never treated as trusted HTML.
export const reportPayload = z
  .object({
    holland_code: z.array(z.string().max(40)).max(6),
    traits: z.record(
      z.string().max(40),
      z
        .object({
          score,
          label: prose,
          blurb: prose,
        })
        .strict(),
    ),
    strengths: z
      .array(
        z
          .object({
            name: prose,
            evidence: prose,
          })
          .strict(),
      )
      .max(50),
    families: z
      .array(
        z
          .object({
            family_id: uuid,
            slug: prose,
            name: prose,
            rank: z.number().int().positive(),
            fit_score: score,
          })
          .strict(),
      )
      .max(50),
    careers: z
      .array(
        z
          .object({
            name: prose,
            family_id: uuid,
            fit_score: score,
            rationale: prose,
            pay: pay.optional(),
            guidance: z
              .object({
                study: prose.optional(),
                project: prose.optional(),
                topic: prose.optional(),
              })
              .strict()
              .optional(),
          })
          .strict(),
      )
      .max(50),
    challenges: z
      .array(
        z
          .object({
            title: prose,
            detail: prose,
          })
          .strict(),
      )
      .max(50),
  })
  .strict();

export const resultInput = z
  .object({
    format_version: z.literal(1),
    payload: reportPayload,
  })
  .strict();
