/**
 * The only module that knows about HTTP.
 *
 * Views and hooks call these functions; nothing above this layer touches `fetch`, URLs or
 * status codes. When the backend contract changes, this file and `types.ts` change — not six
 * components.
 *
 * Requests go to a relative `/api`, which the Vite dev server proxies to FastAPI. Same-origin
 * from the browser's perspective, so CORS doesn't apply in development.
 */

import type {
  AnswerInput,
  Checkout,
  CheckoutStatus,
  Me,
  Pricing,
  Questionnaire,
  QuestionCategory,
  Report,
  StartRequest,
  StartResult,
  SubmitResult,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      headers: { "content-type": "application/json" },
      // The ownership cookie is what makes a response readable at all. Same-origin through
      // the dev proxy wouldn't need this, but a deployment serving the two separately would.
      credentials: "include",
      ...init,
    });
  } catch (cause) {
    // fetch only rejects on network failure, so this is "the server isn't there".
    throw new ApiError(0, "Could not reach the server. Is the backend running?");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }

  // 204 has no body, and json() throws on an empty string — which would reject a request
  // that actually succeeded, and skip whatever was supposed to happen afterwards.
  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

/**
 * FastAPI puts a plain string in `detail` for HTTPException, and a list of field errors there
 * for request-validation failures. The former are messages we wrote for users, so they're worth
 * surfacing verbatim.
 */
async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      const first = body.detail[0] as { msg?: string } | undefined;
      if (first?.msg) return first.msg;
    }
  } catch {
    // Not JSON — fall through to the generic message.
  }
  return `Request failed (${response.status})`;
}

export const api = {
  questionnaire: (responseId?: string) =>
    request<Questionnaire>(
      responseId ? `/questionnaire?response_id=${encodeURIComponent(responseId)}` : "/questionnaire",
    ),

  start: (body: StartRequest) =>
    request<StartResult>("/responses", { method: "POST", body: JSON.stringify(body) }),

  submitSection: (responseId: string, section: QuestionCategory, answers: AnswerInput[]) =>
    request<SubmitResult>(`/responses/${responseId}/sections/${section}`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    }),

  report: (reportId: string) => request<Report>(`/reports/${reportId}`),

  /**
   * The newest report for a response.
   *
   * Reports are append-only, so "the report for this response" is the newest one — which
   * is the most complete. A visitor who has just paid has a Stripe session id rather than a
   * report id, and this is how they get back to the right one.
   */
  latestReport: (responseId: string) => request<Report>(`/responses/${responseId}/report`),

  /**
   * The download URL, not a fetch. A plain link lets the browser handle the save dialog and
   * the filename from Content-Disposition; fetching the bytes ourselves would mean
   * reconstructing both.
   */
  reportPdfUrl: (reportId: string) => `/api/reports/${reportId}/pdf`,

  me: () => request<Me>("/auth/me"),

  logout: () => request<void>("/auth/logout", { method: "POST" }),

  /**
   * A URL, not a fetch. Sign-in is a full-page navigation to Google and back — following it
   * with fetch would land the redirect inside XHR, where the browser can't show Google's
   * consent screen.
   */
  googleLoginUrl: "/api/auth/google/login",

  // ------------------------------------------------------------ payments

  /**
   * What the report costs. Fetched rather than hard-coded, so the price shown is the price
   * charged.
   */
  pricing: () => request<Pricing>("/pricing"),

  /**
   * Start a checkout. The response carries the URL to navigate to — the browser leaves the
   * app for Stripe's page, so this is a POST followed by a navigation, not a fetch that
   * stays in the app.
   */
  createCheckout: (responseId: string, discountCode?: string) =>
    request<Checkout>(`/responses/${responseId}/checkout`, {
      method: "POST",
      body: JSON.stringify(discountCode ? { discount_code: discountCode } : {}),
    }),

  /**
   * Whether the return from Stripe has resulted in access.
   *
   * Asked on the way back, because the webhook that grants access may not have landed yet
   * — and a visitor who has just paid must not be told they have nothing.
   */
  checkoutStatus: (sessionId: string) =>
    request<CheckoutStatus>(`/checkout/${encodeURIComponent(sessionId)}`),
  applyDiscount: (responseId: string, discountCode: string) =>
    request<void>(`/responses/${responseId}/apply_discount`, {
      method: "POST",
      body: JSON.stringify({ discount_code: discountCode }),
    }),
};
