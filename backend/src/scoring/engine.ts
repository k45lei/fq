import {
  careers,
  instruments,
  referenceData,
  type Responses,
  type Scores,
  type Career,
} from './instrument.js';

const keys = (values: Scores) =>
  Object.keys(values).sort((left, right) => values[right] - values[left]);
const clamp = (value: number) => Math.max(0, Math.min(100, value));
const average = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length;
const empty = (names: string[]) => Object.fromEntries(names.map((name) => [name, 0]));
const axes = ['pace', 'collab', 'energy', 'variety'];
const dimensions = ['SD', 'AD', 'SP', 'FG'];
const domains = ['SA', 'SR', 'MO', 'EM', 'SS'];

export const scoreInterests = (answers: Responses) => {
  const calculate = (field: 'riasec' | 'skill' | 'value', names: string[]) => {
    return Object.fromEntries(
      names.map((name) => {
        const values = instruments.interests
          .filter((question) => question[field] === name)
          .map((question) => answers[question.id] as number);

        return [name, Math.round(((average(values) - 1) / 4) * 100)];
      }),
    );
  };

  const riasec = calculate('riasec', ['R', 'I', 'A', 'S', 'E', 'C']);
  const skills = calculate('skill', Object.keys(referenceData.skills));
  const values = calculate('value', [
    'impact',
    'stability',
    'autonomy',
    'creativity',
    'income',
    'learning',
  ]);
  const topRiasec = keys(riasec).slice(0, 3);

  return {
    riasec,
    skills,
    values,
    topRiasec,
    topSkills: keys(skills).slice(0, 3),
    topValues: keys(values).slice(0, 3),
    hollandCode: topRiasec.join(''),
  };
};

const strengthsArchetype = (scores: Scores, motivators: Scores, environment: Scores) => {
  const top = Object.keys(motivators).reduce((best, key) =>
    motivators[best] >= motivators[key] ? best : key,
  );
  const independent = scores.SD >= 65;
  const driven = scores.AD >= 60;
  const structured = scores.SP >= 60;
  const collaborative = environment.collab > 20;
  const archetypes = referenceData.strengthsArchetypes;

  if (independent && driven && !structured) {
    return archetypes[0];
  }
  if (driven && collaborative && top === 'connection') {
    return archetypes[1];
  }
  if (structured && !independent && top === 'mastery') {
    return archetypes[2];
  }
  if (!structured && environment.variety > 20 && top === 'creativity') {
    return archetypes[3];
  }
  if (top === 'impact' && collaborative) {
    return archetypes[4];
  }
  if (driven && !collaborative && ['mastery', 'autonomy'].includes(top)) {
    return archetypes[5];
  }
  if (scores.FG >= 60 && structured) {
    return archetypes[6];
  }

  return archetypes[7];
};

export const scoreStrengths = (answers: Responses) => {
  const totals = empty(dimensions);
  const counts = empty(dimensions);
  const motivators = empty([
    'mastery',
    'status',
    'autonomy',
    'connection',
    'security',
    'impact',
    'creativity',
  ]);
  const environmentTotals = empty(axes);
  const environmentCounts = empty(axes);
  const addEnvironment = (axis: string, value: number) => {
    environmentTotals[axis] += value;
    environmentCounts[axis] += 1;
  };

  for (const question of instruments.strengths) {
    const answer = answers[question.id];

    if (question.type === 'likert' && dimensions.includes(question.dim!)) {
      const normalized = ((answer as number) - 1) / 4;
      totals[question.dim!] += question.weight === 1 ? normalized : 1 - normalized;
      counts[question.dim!] += 1;
    }

    if (question.type === 'forced_choice_multi') {
      for (const id of answer as string[]) {
        const option = question.options!.find((option) => option.id === id)!;
        motivators[option.motivator!] += question.id === 'md-fc-02' ? -1 : 2;
      }
    }

    if (question.type === 'bipolar') {
      addEnvironment(question.axis!, answer as number);
    }

    if (question.type !== 'scenario') {
      continue;
    }

    const option = question.options!.find((option) => option.id === answer)!;

    for (const [dimension, weight] of Object.entries(option.weights ?? {})) {
      if (dimensions.includes(dimension)) {
        totals[dimension] += (Math.max(-2, Math.min(2, weight)) + 2) / 4;
        counts[dimension] += 1;
      }
    }

    if (question.id === 'md-sc-01') {
      const mapping: Record<string, string> = { a: 'mastery', b: 'status', c: 'security' };
      const motivator = mapping[answer as string];
      if (motivator) {
        motivators[motivator] += 1;
      }
    }

    if (question.id === 'md-sc-02') {
      if (answer === 'a') {
        motivators.security += 1;
        motivators.autonomy += 1;
      } else if (answer === 'b') {
        motivators.impact += 2;
      } else if (answer === 'c') {
        motivators.autonomy += 1;
        motivators.mastery += 1;
      }
    }

    if (['we-sc-01', 'we-sc-02'].includes(question.id)) {
      if (answer === 'a') {
        const value = question.id === 'we-sc-01' ? -2 : -1;
        addEnvironment('collab', value);
        addEnvironment('energy', value);
      } else if (answer === 'b') {
        addEnvironment('collab', 2);
        addEnvironment('energy', 2);
      } else if (answer === 'c') {
        addEnvironment('variety', question.id === 'we-sc-01' ? 2 : 1);
      }
    }
  }

  const scores = Object.fromEntries(
    dimensions.map((name) => [name, Math.round((totals[name] / counts[name]) * 100)]),
  );
  const minimum = Math.min(...Object.values(motivators));
  const range = Math.max(...Object.values(motivators)) - minimum || 1;
  const normalizedMotivators = Object.fromEntries(
    referenceData.motivators.map((item) => [
      item.id,
      Math.round(((motivators[item.id] - minimum) / range) * 100),
    ]),
  );
  const environment = Object.fromEntries(
    axes.map((axis) => [
      axis,
      Math.round((environmentTotals[axis] / environmentCounts[axis]) * 50),
    ]),
  );

  // Preserve the reference's ordered smoothing, including its reverse-scored collaboration item.
  const workItems: [string, string, number][] = [
    ['we-01', 'collab', 1],
    ['we-02', 'collab', -1],
    ['we-03', 'energy', 1],
    ['we-04', 'variety', 1],
    ['we-05', 'pace', 1],
    ['we-06', 'variety', 1],
  ];

  for (const [id, axis, direction] of workItems) {
    const signal = ((answers[id] as number) - 3) * direction * 25;
    environment[axis] = Math.round((environment[axis] + signal) / 2);
  }

  // MD Likert items are contextual in the reference; motivator scores come from choices/scenarios.
  return {
    dimensions: scores,
    motivators: normalizedMotivators,
    topMotivators: keys(normalizedMotivators).slice(0, 3),
    workEnv: environment,
    archetype: strengthsArchetype(scores, normalizedMotivators, environment),
  };
};

const eqArchetype = (scores: Scores) => {
  const [first, second, third] = keys(scores);
  let id = 'reflective-strategist';

  if ([first, second].includes('EM') && [first, second].includes('SS')) {
    id = scores.SR >= 60 || scores.SA >= 60 ? 'adaptive-mediator' : 'empathic-connector';
  } else if ([first, second].includes('SA') && [first, second].includes('SR')) {
    id = 'reflective-strategist';
  } else if (first === 'SR' && scores.SR >= 70) {
    id = 'steady-anchor';
  } else if (
    (first === 'MO' && [second, third].includes('SS')) ||
    (first === 'SS' && second === 'MO')
  ) {
    id = 'inspiring-catalyst';
  } else if (first === 'EM') {
    id = 'empathic-connector';
  } else if (first === 'MO') {
    id = 'driven-growth-seeker';
  }

  return referenceData.eqArchetypes.find((archetype) => archetype.id === id)!;
};

export const scoreEq = (answers: Responses) => {
  const totals = empty(domains);
  const maximums = empty(domains);
  const minimums = empty(domains);

  for (const question of instruments.eq) {
    if (question.type === 'likert') {
      totals[question.dim!] += ((answers[question.id] as number) - 3) * question.weight!;
      maximums[question.dim!] += 2;
      minimums[question.dim!] -= 2;
      continue;
    }

    const option = question.options!.find((option) => option.id === answers[question.id])!;

    for (const [domain, weight] of Object.entries(option.weights!)) {
      totals[domain] += weight;
      const weights = question.options!.map((option) => option.weights?.[domain] ?? 0);
      maximums[domain] += Math.max(...weights, 0);
      minimums[domain] += Math.min(...weights, 0);
    }
  }

  const scores = Object.fromEntries(
    domains.map((domain) => [
      domain,
      clamp(
        Math.round(
          ((totals[domain] - minimums[domain]) / (maximums[domain] - minimums[domain])) * 100,
        ),
      ),
    ]),
  );
  const ranked = keys(scores);

  return {
    domains: scores,
    overall: Math.round(average(Object.values(scores))),
    topDomains: ranked.slice(0, 2),
    growthDomain: ranked.at(-1)!,
    archetype: eqArchetype(scores),
  };
};

export const rankInterests = (profile: ReturnType<typeof scoreInterests>) => {
  return careers
    .map((career) => {
      const weightSum = career.holland.reduce(
        (sum, _, index) => sum + career.holland.length - index,
        0,
      );
      const interest =
        career.holland.reduce(
          (sum, trait, index) => sum + profile.riasec[trait] * (career.holland.length - index),
          0,
        ) / weightSum;
      const ability = average(career.skills.map((skill) => profile.skills[skill]));

      return { career, score: Math.round(((interest / 100) * 0.7 + (ability / 100) * 0.3) * 100) };
    })
    .sort((left, right) => right.score - left.score);
};

export const rankStrengths = (profile: ReturnType<typeof scoreStrengths>) => {
  return careers
    .map((career) => {
      const environment = clamp(
        Math.round(
          average(
            axes.map((axis) => 1 - Math.abs(profile.workEnv[axis] - career.env[axis] * 50) / 200),
          ) * 100,
        ),
      );
      const motivation = clamp(
        Math.round(
          average(career.motivators.map((motivator) => profile.motivators[motivator] / 100)) * 100,
        ),
      );
      const autonomy = 1 - Math.abs(profile.dimensions.SD - career.autonomy) / 100;
      const structure = 1 - Math.abs(profile.dimensions.SP - career.structure) / 100;
      const workStyle = clamp(Math.round(((autonomy + structure) / 2) * 100));

      return {
        career,
        score: clamp(Math.round(environment * 0.4 + motivation * 0.4 + workStyle * 0.2)),
      };
    })
    .sort((left, right) => right.score - left.score);
};

export const rankEq = (profile: ReturnType<typeof scoreEq>) => {
  return careers
    .map((career) => {
      let total = 0;
      let weightSum = 0;

      for (const [domain, weight] of Object.entries(career.eqProfile)) {
        if (weight <= 0) {
          continue;
        }
        const target = career.eqTargets[domain] ?? 60;
        const fit =
          profile.domains[domain] >= target
            ? 100
            : Math.max(0, 100 - (target - profile.domains[domain]) * 1.5);
        total += fit * weight;
        weightSum += weight;
      }

      return { career, score: Math.round(total / weightSum) };
    })
    .sort((left, right) => right.score - left.score);
};

export const confidence = (spread: number): 'high' | 'medium' | 'mixed' => {
  if (spread <= 15) {
    return 'high';
  }
  if (spread <= 30) {
    return 'medium';
  }

  return 'mixed';
};

export const combine = (
  interests: ReturnType<typeof rankInterests>,
  strengths: ReturnType<typeof rankStrengths>,
  eq: ReturnType<typeof rankEq>,
) => {
  const strengthScores = new Map(strengths.map((item) => [item.career.id, item.score]));
  const eqScores = new Map(eq.map((item) => [item.career.id, item.score]));

  return interests
    .map((item) => {
      const strength = strengthScores.get(item.career.id)!;
      const emotional = eqScores.get(item.career.id)!;
      const spread =
        Math.max(item.score, strength, emotional) - Math.min(item.score, strength, emotional);

      return {
        career: item.career,
        score: Math.round(item.score * 0.4 + strength * 0.3 + emotional * 0.3),
        signals: { interests: item.score, strengths: strength, eq: emotional },
        spread,
        confidence: confidence(spread),
      };
    })
    .sort((left, right) => right.score - left.score);
};

export const careerFamily = (career: Career) => {
  const identity = (career.id + career.title).toLowerCase();
  const families: [RegExp, string][] = [
    [
      /mech-engineer|electrician|pilot|welder|carpenter|hvac|auto|plumber|construction|technician|geologist/,
      'Building, Trades & Technical',
    ],
    [
      /data|software|dev|programmer|scientist|research|engineer|analyst|actuary|statistician|ai|mathematician/,
      'Analytical & Technical',
    ],
    [
      /designer|architect|artist|writer|filmmaker|animator|musician|photographer|producer|content|creative/,
      'Creative & Design',
    ],
    [
      /doctor|nurse|therapist|psychologist|social-worker|teacher|counsellor|dentist|pharmacist|paramedic|physio|coach|educator/,
      'People-Serving & Care',
    ],
    [
      /marketing|sales|entrepreneur|founder|manager|consultant|lawyer|realtor|hr|finance|banker/,
      'Business & Leadership',
    ],
    [
      /accountant|logistics|operations|admin|planner|inspector|paralegal|clerk/,
      'Organizing & Operations',
    ],
  ];

  return families.find(([pattern]) => pattern.test(identity))?.[1] ?? 'Other paths';
};
