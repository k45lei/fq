import type { QuestionCategory } from "../api/types";

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
      "Explore six career interest types alongside your self-reported abilities and values.",
    asks: [
      "Activities you'd actually choose on a free day",
      "The abilities you enjoy using",
      "What matters to you in work",
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
      "Explore self-awareness, self-regulation, motivation, empathy, and social skills.",
    asks: [
      "How you handle things going wrong",
      "Whether you can disagree without it getting personal",
      "What criticism does to you",
    ],
  },
};
