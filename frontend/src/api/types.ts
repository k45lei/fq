/**
 * Mirrors the Pydantic schemas in `backend/app/schemas.py`.
 *
 * Hand-written, so it can drift. The upgrade is generating this from FastAPI's /openapi.json
 * with `openapi-typescript`; worth doing once the contract settles.
 *
 * Nothing outside `src/api/` should import HTTP concerns — these types are the contract, and
 * everything above this layer works in them.
 */

export type AnswerType = "single_choice" | "multi_choice" | "scale" | "free_text";
export type QuestionCategory = "interests" | "strengths" | "eq";
export type ReportTier = "free" | "full";

export interface Option {
  id: string;
  label: string;
}

export interface Question {
  id: string;
  position: number;
  category: QuestionCategory;
  answer_type: AnswerType;
  prompt: string;
  help_text: string | null;
  scale_min: number | null;
  scale_max: number | null;
  max_selections: number | null;
  options: Option[];
}

export interface Questionnaire {
  id: string;
  nickname: string;
  version: string;
  /**
   * Which sections this caller may answer, in presentation order. Normally just `interests`;
   * all three when the server's entitlement gate returns a row for this response. Each
   * question carries its own `category`, so grouping needs no second lookup.
   */
  sections: QuestionCategory[];
  /**
   * Which sections this response has already had submitted.
   *
   * A section can only be submitted once, so a client that loops over `sections` has to skip
   * these — otherwise a visitor who paid after finishing the free tier resubmits it and is
   * refused with a 422.
   */
  completed_sections: QuestionCategory[];
  questions: Question[];
}

export interface StartRequest {
  name: string;
  email: string;
  school?: string | null;
}

export interface StartResult {
  response_id: string;
  questionnaire: Questionnaire;
}

/** One answer, shaped by its question's `answer_type`. The server validates the pairing. */
export interface AnswerInput {
  question_id: string;
  numeric?: number;
  text?: string;
  option_ids?: string[];
}

export interface SubmitResult {
  report_id: string;
}

export interface TraitScore {
  score: number;
  label: string;
  blurb: string;
}

export type PaySource = "bls_oews" | "model_research" | "mock";

/** Optional by design: a career_pay miss omits the figure rather than inventing one. */
export interface Pay {
  low: number | null;
  median: number | null;
  high: number | null;
  currency: string;
  /** `mock` means a placeholder for demos, not sourced data. Show it as such. */
  source: PaySource;
  source_url: string | null;
  as_of: string;
}

/** What to do about a career. Each field is absent when there's nothing to say. */
export interface Guidance {
  study?: string;
  project?: string;
  topic?: string;
}

export interface CareerSuggestion {
  name: string;
  family_id: string;
  fit_score: number;
  rationale: string;
  pay?: Pay;
  guidance?: Guidance;
}

export interface RankedFamily {
  family_id: string;
  slug: string;
  name: string;
  rank: number;
  fit_score: number;
}

export interface ReportPayload {
  holland_code: string[];
  traits: Record<string, TraitScore>;
  strengths: { name: string; evidence: string }[];
  families: RankedFamily[];
  careers: CareerSuggestion[];
  challenges: { title: string; detail: string }[];
}

export interface PastReport {
  id: string;
  generated_at: string;
  tier: ReportTier;
}

/** Who's signed in. `signed_in: false` is the normal anonymous answer, not an error. */
export interface Me {
  signed_in: boolean;
  name?: string | null;
  email?: string | null;
  reports: PastReport[];
}

export interface Report {
  id: string;
  response_id: string;
  tier: ReportTier;
  generated_at: string;
  payload: ReportPayload;
}

// ---------------------------------------------------------------- payments

/**
 * What the report costs.
 *
 * From the server, never hard-coded here: the number the paywall shows must be the number
 * Stripe charges, and a figure written into the frontend is a figure nobody maintains.
 */
export interface Pricing {
  amount_cents: number;
  currency: string;
}

/**
 * Where to send the browser, and which session to come back to.
 *
 * A URL and an id, not Stripe's session object — the contract is ours, not theirs.
 */
export interface Checkout {
  url: string;
  session_id: string;
}

/**
 * Whether a checkout has resulted in access, checked on the browser's return.
 *
 * The webhook that grants access may not have landed yet, so the return page asks rather
 * than assumes.
 */
export interface CheckoutStatus {
  session_id: string;
  paid: boolean;
  entitled: boolean;
  /** The response the purchase was for, when the session names one. */
  response_id: string | null;
}
