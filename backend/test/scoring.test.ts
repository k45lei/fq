import assert from 'node:assert/strict';
import { test } from 'node:test';
import fixtures from './fixtures/scoring-parity.json' with { type: 'json' };
import {
  scoreInterests,
  scoreStrengths,
  scoreEq,
  rankInterests,
  rankStrengths,
  rankEq,
  combine,
} from '../src/scoring/engine.js';
import { type Responses, instrumentRows } from '../src/scoring/instrument.js';

// Expected results were captured from the supplied reference bundle, not the port.
for (const [index, fixture] of fixtures.entries()) {
  test(`reference scoring parity: fixture ${index}`, () => {
    const interests = scoreInterests(fixture.answers.interests);
    const strengths = scoreStrengths(fixture.answers.strengths as Responses);
    const eq = scoreEq(fixture.answers.eq);
    assert.deepEqual(interests, fixture.expected.interests);
    assert.deepEqual(strengths, fixture.expected.strengths);
    assert.deepEqual(eq, fixture.expected.eq);

    const interestCareers = rankInterests(interests);
    const strengthCareers = rankStrengths(strengths);
    const eqCareers = rankEq(eq);
    const simplify = (items: typeof interestCareers) =>
      items.map((item) => ({ id: item.career.id, score: item.score }));
    assert.deepEqual(simplify(interestCareers.slice(0, 8)), fixture.expected.interestCareers);
    assert.deepEqual(simplify(strengthCareers), fixture.expected.strengthCareers);
    assert.deepEqual(simplify(eqCareers), fixture.expected.eqCareers);

    const combined = combine(interestCareers, strengthCareers, eqCareers);
    const topInterestIds = new Set(interestCareers.slice(0, 3).map((item) => item.career.id));
    const matches = {
      topMatches: combined.slice(0, 5),
      hiddenGems: combined
        .filter(
          (item) =>
            !topInterestIds.has(item.career.id) && item.score >= 65 && item.confidence !== 'mixed',
        )
        .slice(0, 3),
      watchOuts: combined
        .slice(0, 10)
        .filter((item) => item.confidence === 'mixed')
        .slice(0, 3),
    };

    for (const key of ['topMatches', 'hiddenGems', 'watchOuts'] as const) {
      assert.deepEqual(
        matches[key].map((item) => ({
          id: item.career.id,
          score: item.score,
          confidence: item.confidence,
        })),
        fixture.expected.compass[key],
      );
    }
  });
}

test('instrument has stable unique identifiers and exact forced-choice limits', () => {
  const rows = instrumentRows();
  assert.deepEqual(
    ['interests', 'strengths', 'eq'].map(
      (stage) => rows.filter((row) => row.category === stage).length,
    ),
    [32, 76, 30],
  );
  assert.equal(new Set(rows.map((row) => row.id)).size, 138);
  assert.deepEqual(
    rows
      .filter((row) => row.answerType === 'multi_choice')
      .map((row) => [row.minSelections, row.maxSelections]),
    [
      [3, 3],
      [2, 2],
    ],
  );
});
