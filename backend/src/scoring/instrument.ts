import { createHash } from 'node:crypto';
import reference from './reference.json' with { type: 'json' };

export type Stage = 'interests' | 'strengths' | 'eq';
export type Scores = Record<string, number>;
export type Responses = Record<string, number | string | string[]>;

export interface ReferenceQuestion {
  id: string;
  section: string;
  text?: string;
  type?: 'likert' | 'scenario' | 'forced_choice_multi' | 'bipolar';
  riasec?: string;
  skill?: string;
  value?: string;
  dim?: string;
  weight?: number;
  pickCount?: number;
  axis?: string;
  poleA?: string;
  poleB?: string;
  options?: {
    id: string;
    label: string;
    motivator?: string;
    weights?: Scores;
  }[];
}

export interface Career {
  id: string;
  title: string;
  holland: string[];
  skills: string[];
  blurb: string;
  subjects: string[];
  explore: string[];
  env: Scores;
  motivators: string[];
  autonomy: number;
  structure: number;
  eqProfile: Scores;
  eqTargets: Scores;
}

export const INSTRUMENT_VERSION = 'reference-138-v1';
export const RULESET_VERSION = 'reference-rules-v1';
export const referenceData = reference;
export const instruments: Record<Stage, ReferenceQuestion[]> = {
  interests: reference.interests,
  strengths: reference.strengths as ReferenceQuestion[],
  eq: reference.eq as ReferenceQuestion[],
};
export const careers = reference.careers as Career[];
export const stableId = (key: string) => {
  const hex = createHash('sha256').update(`futurequest:${INSTRUMENT_VERSION}:${key}`).digest('hex');

  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
};

export const instrumentRows = () => {
  let position = 0;

  return Object.entries(instruments).flatMap(([category, questions]) =>
    questions.map((question) => {
      position += 1;
      const referenceKey = `${category}:${question.id}`;
      const bipolar = question.type === 'bipolar';
      const choice = question.type === 'scenario' || question.type === 'forced_choice_multi';
      const scaleMin = bipolar ? -2 : 1;
      const scaleMax = bipolar ? 2 : 5;
      let answerType: 'scale' | 'single_choice' | 'multi_choice' = 'scale';

      if (question.type === 'scenario') {
        answerType = 'single_choice';
      } else if (question.type === 'forced_choice_multi') {
        answerType = 'multi_choice';
      }

      return {
        id: stableId(referenceKey),
        referenceKey,
        position,
        category: category as Stage,
        subsection: question.section,
        answerType,
        prompt: question.text ?? 'Which work environment suits you?',
        helpText: question.pickCount ? `Choose exactly ${question.pickCount}.` : null,
        scaleMin: choice ? null : scaleMin,
        scaleMax: choice ? null : scaleMax,
        scaleMinLabel: choice ? null : (question.poleA ?? 'Strongly disagree'),
        scaleMaxLabel: choice ? null : (question.poleB ?? 'Strongly agree'),
        minSelections: question.pickCount ?? null,
        maxSelections: question.pickCount ?? null,
        options: (question.options ?? []).map((option, index) => ({
          id: stableId(`${referenceKey}:${option.id}`),
          referenceKey: option.id,
          position: index + 1,
          label: option.label,
        })),
      };
    }),
  );
};

export const validateInstrument = () => {
  const rows = instrumentRows();

  if (rows.length !== 138 || new Set(rows.map((row) => row.id)).size !== rows.length) {
    throw new Error('Instrument contains missing or duplicate questions');
  }

  const traitKeys = Object.keys(referenceData.traits);
  const skillKeys = Object.keys(referenceData.skills);
  const motivatorKeys = referenceData.motivators.map((item) => item.id);
  const axes = ['pace', 'collab', 'energy', 'variety'];
  const eqDomains = referenceData.eqDimensions.map((item) => item.id);

  for (const career of careers) {
    if (
      !career.env ||
      !career.eqProfile ||
      !career.holland.length ||
      !career.skills.length ||
      career.holland.some((key) => !traitKeys.includes(key)) ||
      career.skills.some((key) => !skillKeys.includes(key)) ||
      career.motivators.some((key) => !motivatorKeys.includes(key)) ||
      axes.some((key) => !Number.isFinite(career.env[key])) ||
      Object.keys(career.eqProfile).some((key) => !eqDomains.includes(key)) ||
      Object.values(career.eqProfile).reduce((sum, weight) => sum + weight, 0) <= 0
    ) {
      throw new Error(`Career scoring coverage is incomplete: ${career.id}`);
    }
  }

  for (const question of instruments.interests) {
    if (
      (!question.riasec && !question.skill && !question.value) ||
      (question.riasec && !traitKeys.includes(question.riasec)) ||
      (question.skill && !skillKeys.includes(question.skill)) ||
      (question.value &&
        !['impact', 'stability', 'autonomy', 'creativity', 'income', 'learning'].includes(
          question.value,
        ))
    ) {
      throw new Error(`Interest scoring mapping is missing: ${question.id}`);
    }
  }

  for (const [stage, questions] of Object.entries(instruments)) {
    if (stage === 'interests') {
      continue;
    }
    const allowedDimensions = stage === 'eq' ? eqDomains : ['SD', 'AD', 'SP', 'FG', 'MD', 'WE'];

    for (const question of questions) {
      if (!question.type || (question.dim && !allowedDimensions.includes(question.dim))) {
        throw new Error(`Scoring dimension is missing: ${question.id}`);
      }
      if (question.type === 'likert' && (!question.dim || ![1, -1].includes(question.weight!))) {
        throw new Error(`Scale scoring mapping is missing: ${question.id}`);
      }
      if (
        question.type === 'bipolar' &&
        (!axes.includes(question.axis!) || !question.poleA || !question.poleB)
      ) {
        throw new Error(`Work-environment mapping is missing: ${question.id}`);
      }
      if (question.type === 'scenario' || question.type === 'forced_choice_multi') {
        const options = question.options ?? [];
        if (
          !options.length ||
          new Set(options.map((option) => option.id)).size !== options.length
        ) {
          throw new Error(`Choice mapping is incomplete: ${question.id}`);
        }
        if (
          question.type === 'forced_choice_multi' &&
          (!question.pickCount ||
            question.pickCount > options.length ||
            options.some((option) => !motivatorKeys.includes(option.motivator!)))
        ) {
          throw new Error(`Motivator mapping is incomplete: ${question.id}`);
        }
        if (
          question.type === 'scenario' &&
          options.some((option) =>
            Object.entries(option.weights ?? {}).some(
              ([key, value]) => !allowedDimensions.includes(key) || !Number.isFinite(value),
            ),
          )
        ) {
          throw new Error(`Scenario mapping is invalid: ${question.id}`);
        }
      }
    }
  }
};

validateInstrument();
