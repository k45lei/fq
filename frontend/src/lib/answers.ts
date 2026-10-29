/**
 * Turning draft answers into something submittable.
 *
 * These checks mirror the server's cardinality rules in `backend/app/responses.py`. They exist
 * for the interface — keeping Next disabled until a question is answered — not as a security
 * boundary. The server validates independently and is the authority.
 */

import type { AnswerInput, Question } from "../api/types";
import type { DraftAnswer } from "../store/questionnaire";

export function isAnswered(question: Question, draft: DraftAnswer | undefined): boolean {
  if (!draft) return false;

  switch (question.answer_type) {
    case "scale":
      return typeof draft.numeric === "number";

    case "free_text":
      return Boolean(draft.text?.trim());

    case "single_choice":
      return (draft.optionIds?.length ?? 0) === 1;

    case "multi_choice": {
      const count = draft.optionIds?.length ?? 0;
      if (count === 0) return false;
      return question.max_selections === null || count <= question.max_selections;
    }
  }
}

export function toAnswerInput(question: Question, draft: DraftAnswer): AnswerInput {
  switch (question.answer_type) {
    case "scale":
      return { question_id: question.id, numeric: draft.numeric };
    case "free_text":
      return { question_id: question.id, text: draft.text?.trim() };
    default:
      return { question_id: question.id, option_ids: draft.optionIds ?? [] };
  }
}

export function scaleValues(question: Question): number[] {
  const min = question.scale_min ?? 1;
  const max = question.scale_max ?? 5;
  return Array.from({ length: max - min + 1 }, (_, offset) => min + offset);
}

/** True once every question has a usable answer — i.e. the section can be submitted. */
export function isSectionComplete(
  questions: Question[],
  answers: Record<string, DraftAnswer>,
): boolean {
  return questions.every((question) => isAnswered(question, answers[question.id]));
}
