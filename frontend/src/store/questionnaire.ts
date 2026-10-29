/**
 * In-progress questionnaire state, persisted to localStorage.
 *
 * This *is* the session-recovery requirement from `docs/requirements.md`: answers are saved as
 * they're given, so closing the tab mid-questionnaire loses nothing. Only submitted answers
 * reach the database — everything here is client-side until the section is sent.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

/** One in-progress answer. Which field is meaningful depends on the question's answer_type. */
export interface DraftAnswer {
  numeric?: number;
  text?: string;
  optionIds?: string[];
}

interface QuestionnaireState {
  responseId: string | null;
  /** Keyed by question id. */
  answers: Record<string, DraftAnswer>;
  /**
   * The question the visitor is on, by id.
   *
   * Not an index, because the list of questions is not fixed: the sections a response may
   * answer change when an entitlement is granted, and the questions of a completed section
   * drop out of the list. An index into a list that has been filtered is a position in the
   * wrong place — the visitor resumes in the middle of a section they haven't started. A
   * question id survives both.
   */
  currentQuestionId: string | null;
  /** Set once the section has been submitted, so a resume goes to the report, not the form. */
  reportId: string | null;

  begin: (responseId: string) => void;
  setAnswer: (questionId: string, answer: DraftAnswer) => void;
  goTo: (questionId: string) => void;
  complete: (reportId: string) => void;
  /**
   * The visitor has bought the rest of the questionnaire.
   *
   * Clears the stored report id — it's the free one, and the questionnaire redirects to it
   * on the way in — and the position, so the paid sections start at their beginning. The
   * draft answers are kept: they're the visitor's work, and there's no reason to throw
   * them away.
   */
  unlock: (responseId: string) => void;
  reset: () => void;
}

const EMPTY = {
  responseId: null,
  answers: {} as Record<string, DraftAnswer>,
  currentQuestionId: null,
  reportId: null,
};

export const useQuestionnaireStore = create<QuestionnaireState>()(
  persist(
    (set) => ({
      ...EMPTY,

      // A new attempt starts from nothing: keeping answers from a previous response would
      // submit them against question ids belonging to a different attempt.
      begin: (responseId) => set({ ...EMPTY, responseId }),

      setAnswer: (questionId, answer) =>
        set((state) => ({ answers: { ...state.answers, [questionId]: answer } })),

      goTo: (questionId) => set({ currentQuestionId: questionId }),

      complete: (reportId) => set({ reportId }),

      unlock: (responseId) => set({ responseId, reportId: null, currentQuestionId: null }),

      reset: () => set(EMPTY),
    }),
    {
      name: "futurequest.questionnaire",
      version: 2,
    },
  ),
);
