import assert from "node:assert/strict";
import { test } from "node:test";
import { isAnswered, isSectionComplete, scaleValues, toAnswerInput } from "./answers";
import type { Question } from "../api/types";

const question: Question = {
  id: "question",
  position: 1,
  category: "strengths",
  answer_type: "scale",
  prompt: "Choose a work environment",
  help_text: null,
  scale_min: -2,
  scale_max: 2,
  max_selections: null,
  options: [],
};

test("bipolar answers include zero, preserve negative values, and reject out-of-range values", () => {
  assert.deepEqual(scaleValues(question), [-2, -1, 0, 1, 2]);
  assert.equal(isAnswered(question, { numeric: 0 }), true);
  assert.equal(isAnswered(question, { numeric: -2 }), true);
  assert.equal(isAnswered(question, { numeric: 3 }), false);
  assert.equal(isAnswered(question, { numeric: 0.5 }), false);
  assert.deepEqual(toAnswerInput(question, { numeric: -2 }), {
    question_id: "question",
    numeric: -2,
  });
});

test("forced-choice questions are complete only with the required selection count", () => {
  const forced: Question = {
    ...question,
    answer_type: "multi_choice",
    min_selections: 3,
    max_selections: 3,
  };
  assert.equal(isAnswered(forced, { optionIds: ["a", "b"] }), false);
  assert.equal(isAnswered(forced, { optionIds: ["a", "b", "c"] }), true);
  assert.equal(isAnswered(forced, { optionIds: ["a", "b", "c", "d"] }), false);
  assert.equal(isSectionComplete([forced], { question: { optionIds: ["a"] } }), false);
});
