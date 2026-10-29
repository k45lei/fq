import type { QuestionCategory } from "../api/types";

/**
 * How each section is introduced.
 *
 * `blurb` is the one-liner used in the questionnaire header, where space is tight. `detail`
 * and `asks` are for the landing page, where the job is explaining what the thing actually
 * does — forty questions is a real ask, and people agree to it more readily when they can see
 * what each stretch is for.
 */
/**
 * Every section the product has, in presentation order.
 *
 * Distinct from the server's `questionnaire.sections`, which is only what the caller may
 * *answer*. The difference between the two is exactly the set behind the paywall — which is
 * what the landing page and the teaser both need to know.
 */
export const SECTION_ORDER: QuestionCategory[] = ["interests", "strengths", "eq"];

export interface SectionMeta {
  label: string;
  blurb: string;
  detail: string;
  asks: string[];
}

export const SECTION_META: Record<QuestionCategory, SectionMeta> = {
  interests: {
    label: "Interests",
    blurb: "What kind of work pulls at you.",
    detail:
      "Scored against six established career traits. This is what decides which fields come " +
      "out on top, and it's the part most people have never been asked about directly.",
    asks: [
      "Activities you'd actually choose on a free day",
      "Which school subjects you'd keep",
      "How you'd start a project with no instructions",
    ],
  },
  strengths: {
    label: "Strengths",
    blurb: "What you're already good at, and how you work.",
    detail:
      "Interests tell you the field; this narrows the role. Two people drawn to the same work " +
      "often belong in very different jobs inside it.",
    asks: [
      "What people come to you for",
      "Whether you finish things once they stop being fun",
      "What you'd back yourself to learn quickly",
    ],
  },
  eq: {
    label: "Emotional intelligence",
    blurb: "How you handle pressure, and other people.",
    detail:
      "The part that decides whether a career fits day to day rather than on paper. Almost " +
      "nobody leaves a job because the work was wrong.",
    asks: [
      "How you handle things going wrong",
      "Whether you can disagree without it getting personal",
      "What criticism does to you",
    ],
  },
};
