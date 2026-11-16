/**
 * The return from Stripe.
 *
 * Stripe redirects here the moment the payment completes — which is *not* the same moment
 * access exists. The webhook that writes the purchase and the entitlement may not have
 * landed yet, so this page asks the server rather than assuming. The server verifies the
 * session with Stripe and, if the webhook is behind, performs the same grant inline
 * (see `backend/src/payments.ts`).
 *
 * The query polls while the answer is still "not yet", because the webhook can land a
 * moment after the redirect. A paid session resolves at once; an unpaid one never will, so
 * the polling stops rather than running forever.
 *
 * Payment unlocks the remaining questions. Each completed stage stores a report;
 * its existence alone does not mean all questions have been answered. Continue unanswered
 * sections first, then show the latest stored result or placeholder.
 */

import { useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useCheckoutStatus, useLatestReport, useQuestionnaire } from "../api/queries";
import { checkoutNextStep } from "../lib/checkout";
import Layout from "../components/Layout";
import { useQuestionnaireStore } from "../store/questionnaire";

export default function CheckoutSuccess() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const sessionId = params.get("session_id") ?? undefined;
  const { data, isPending, error } = useCheckoutStatus(sessionId);

  // Checkout provides a session ID. Fetch the attempt's latest report and completion
  // state before deciding whether to show results or continue unanswered sections.
  const { data: report } = useLatestReport(data?.response_id ?? undefined);
  const { data: questionnaire, error: questionnaireError } = useQuestionnaire(
    data?.response_id ?? undefined,
  );
  const unlock = useQuestionnaireStore((state) => state.unlock);

  // Report existence does not imply completion: each stage creates its own report.
  useEffect(() => {
    const next = checkoutNextStep(data, questionnaire, report);

    if (next?.kind === "report") {
      navigate(`/report/${next.reportId}`, { replace: true });
    } else if (next?.kind === "continue") {
      unlock(next.responseId);
      navigate("/questionnaire", { replace: true });
    }
  }, [data, report, questionnaire, unlock, navigate]);

  return (
    <Layout>
      <h1 className="font-display text-4xl font-semibold">Thank you</h1>

      {isPending && <p className="mt-4 text-muted">Confirming your payment…</p>}

      {(error || questionnaireError) && (
        <>
          <p className="mt-4 text-muted text-pretty">
            We couldn&rsquo;t confirm your payment just now. If you were charged, it will still show
            up — email us and we&rsquo;ll check.
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link
              to="/"
              className="rounded-xl bg-accent px-6 py-3 font-medium text-white transition hover:bg-accent-strong"
            >
              Back to the start
            </Link>
          </div>
        </>
      )}

      {data && !data.entitled && (
        <>
          <p className="mt-4 text-muted text-pretty">
            {data.payment_status === "refunded"
              ? "This payment was refunded. Paid access is no longer available."
              : "Payment has not been confirmed yet. Access will unlock once confirmation arrives."}
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link
              to="/"
              className="rounded-xl bg-accent px-6 py-3 font-medium text-white transition hover:bg-accent-strong"
            >
              Back to the start
            </Link>
          </div>
        </>
      )}

      {data?.entitled && <p className="mt-4 text-muted">Taking you back to your questionnaire…</p>}
    </Layout>
  );
}
