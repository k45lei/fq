import { z } from 'zod';
import { reportPayloadV2 } from '../domain.js';
import {
  instruments,
  referenceData,
  stableId,
  type Career,
  type Responses,
  type Stage,
} from './instrument.js';
import {
  scoreInterests,
  scoreStrengths,
  scoreEq,
  rankInterests,
  rankStrengths,
  rankEq,
  combine,
  confidence,
  careerFamily,
} from './engine.js';

const snapshotSchema = z.array(
  z.object({
    category: z.enum(['interests', 'strengths', 'eq']),
    reference_key: z.string(),
    numeric: z.number().nullable(),
    selected_options: z.array(z.object({ reference_key: z.string() })),
  }),
);

export const generateReport = (stage: Stage, snapshots: unknown) => {
  const input = snapshotSchema.parse(snapshots);
  const answers: Record<Stage, Responses> = { interests: {}, strengths: {}, eq: {} };
  const activeStages = (['interests', 'strengths', 'eq'] as const).slice(
    0,
    ['interests', 'strengths', 'eq'].indexOf(stage) + 1,
  );

  for (const section of activeStages) {
    for (const question of instruments[section]) {
      const answer = input.find(
        (answer) =>
          answer.reference_key === `${section}:${question.id}` && answer.category === section,
      );

      if (!answer) {
        throw new Error(`Missing scoring input: ${section}:${question.id}`);
      }

      if (question.type === 'scenario' || question.type === 'forced_choice_multi') {
        const choices = answer.selected_options.map((option) => option.reference_key);

        if (
          choices.some((id) => !question.options!.some((option) => option.id === id)) ||
          choices.length !== (question.pickCount ?? 1) ||
          new Set(choices).size !== choices.length
        ) {
          throw new Error(`Invalid scoring choices: ${question.id}`);
        }

        answers[section][question.id] = question.type === 'scenario' ? choices[0] : choices;
      } else {
        const min = question.type === 'bipolar' ? -2 : 1;
        const max = question.type === 'bipolar' ? 2 : 5;

        if (
          answer.numeric === null ||
          !Number.isInteger(answer.numeric) ||
          answer.numeric < min ||
          answer.numeric > max
        ) {
          throw new Error(`Invalid numeric scoring input: ${question.id}`);
        }

        answers[section][question.id] = answer.numeric;
      }
    }
  }

  const interestProfile = scoreInterests(answers.interests);
  const strengthProfile = stage !== 'interests' ? scoreStrengths(answers.strengths) : null;
  const emotionalProfile = stage === 'eq' ? scoreEq(answers.eq) : null;
  const interestMatches = rankInterests(interestProfile);
  const strengthMatches = strengthProfile ? rankStrengths(strengthProfile) : [];
  const emotionalMatches = emotionalProfile ? rankEq(emotionalProfile) : [];
  const combined = emotionalProfile
    ? combine(interestMatches, strengthMatches, emotionalMatches)
    : [];
  const traits = referenceData.traits as Record<
    string,
    { name: string; description: string; tagline: string }
  >;
  const skills = referenceData.skills as Record<string, { name: string; description: string }>;
  const leadingTrait = traits[interestProfile.topRiasec[0]];
  let matches = interestMatches.slice(0, 8);

  if (stage === 'strengths') {
    // The cumulative report retains interests and adds a separate strengths profile/ranking.
    matches = strengthMatches.slice(0, 8);
  } else if (stage === 'eq') {
    matches = combined.slice(0, 5);
  }

  const toMatch = (match: (typeof combined)[number]) => ({
    career_id: match.career.id,
    name: match.career.title,
    fit_score: match.score / 100,
    confidence: match.confidence,
    signals: {
      interests: match.signals.interests / 100,
      strengths: match.signals.strengths / 100,
      eq: match.signals.eq / 100,
    },
  });
  const topInterests = new Set(interestMatches.slice(0, 3).map((match) => match.career.id));
  const abilities = interestProfile.topSkills.map((key) => ({
    name: skills[key].name,
    score: interestProfile.skills[key] / 100,
    description: skills[key].description,
  }));

  const careerRationale = (career: Career) => {
    if (stage === 'eq') {
      return 'Ranked using your interests, strengths, and EQ together.';
    }

    if (stage === 'strengths') {
      return 'Matches your preferred work environment, motivators, and work style.';
    }

    const interests = career.holland.map((key) => traits[key].tagline.toLowerCase()).join(' and ');

    return `Your interest in ${interests} work and your abilities contribute to this match.`;
  };

  return reportPayloadV2.parse({
    headline: interestProfile.topRiasec
      .slice(0, 2)
      .map((key) => traits[key].name)
      .join(' + '),
    summary: leadingTrait.description,
    holland_code: interestProfile.topRiasec,
    traits: Object.fromEntries(
      Object.entries(interestProfile.riasec).map(([key, value]) => [
        key,
        {
          score: value / 100,
          label: traits[key].name,
          blurb: traits[key].description,
        },
      ]),
    ),
    abilities,
    trait_profiles: interestProfile.topRiasec.slice(0, 2).map((key) => {
      const detail = referenceData.traitProfiles[key as keyof typeof referenceData.traitProfiles];
      return {
        name: traits[key].name,
        headline: detail.headline,
        score: interestProfile.riasec[key] / 100,
        strengths: detail.strengths,
        environments: detail.environments,
        example_work: detail.sampleWork,
      };
    }),
    motivators: strengthProfile
      ? referenceData.motivators.map((item) => ({
          name: item.label,
          description: item.description,
          score: strengthProfile.motivators[item.id] / 100,
        }))
      : [],
    work_environment: strengthProfile
      ? instruments.strengths
          .filter((question) => question.type === 'bipolar')
          .map((question) => ({
            name: question.axis!,
            score: strengthProfile.workEnv[question.axis!],
            low: question.poleA!,
            high: question.poleB!,
          }))
      : [],
    values: interestProfile.topValues.map((key) => ({
      name: key.charAt(0).toUpperCase() + key.slice(1),
      score: interestProfile.values[key] / 100,
      description: 'Based on what you said matters to you in work.',
    })),
    strengths: abilities.map((ability) => ({ name: ability.name, evidence: ability.description })),
    families: [...new Set(matches.map((match) => careerFamily(match.career)))].map(
      (name, index) => ({
        family_id: stableId(`family:${name}`),
        slug: name.toLowerCase().replace(/[^a-z]+/g, '-'),
        name,
        rank: index + 1,
        fit_score: matches.find((match) => careerFamily(match.career) === name)!.score / 100,
      }),
    ),
    careers: matches.map(({ career, score }) => ({
      career_id: career.id,
      family_id: stableId(`family:${careerFamily(career)}`),
      name: career.title,
      description: career.blurb,
      fit_score: score / 100,
      rationale: careerRationale(career),
      subjects: career.subjects,
      activities: career.explore,
      guidance: { project: career.explore[0], topic: career.subjects.join(', ') },
    })),
    challenges: [],
    strength_profile: strengthProfile
      ? {
          name: strengthProfile.archetype.label,
          description: strengthProfile.archetype.blurb,
          scores: referenceData.strengthsDimensions
            .filter((item) => item.id in strengthProfile.dimensions)
            .map((item) => ({
              name: item.label,
              description: item.description,
              score: strengthProfile.dimensions[item.id] / 100,
            })),
        }
      : null,
    eq_profile: emotionalProfile
      ? {
          name: emotionalProfile.archetype.label,
          description: emotionalProfile.archetype.blurb,
          scores: referenceData.eqDimensions.map((item) => ({
            name: item.label,
            description: item.description,
            score: emotionalProfile.domains[item.id] / 100,
          })),
        }
      : null,
    compass: combined.length
      ? {
          confidence: confidence(
            combined.slice(0, 5).reduce((sum, match) => sum + match.spread, 0) / 5,
          ),
          top_matches: combined.slice(0, 5).map(toMatch),
          hidden_gems: combined
            .filter(
              (match) =>
                !topInterests.has(match.career.id) &&
                match.score >= 65 &&
                match.confidence !== 'mixed',
            )
            .slice(0, 3)
            .map(toMatch),
          watch_outs: combined
            .slice(0, 10)
            .filter((match) => match.confidence === 'mixed')
            .slice(0, 3)
            .map(toMatch),
        }
      : null,
  });
};
