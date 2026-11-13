/**
 * The return from Stripe.
 *
 * Stripe redirects here the moment the payment completes — which is *not* the same moment
 * access exists. The webhook that writes the purchase and the entitlement may not have
 * landed yet, so this page asks the server rather than assuming. The server verifies the
 * session with Stripe and, if the webhook is behind, performs the same grant inline
 * (see `backend/app/routes/payments.py`).
 *
 * The query polls while the answer is still "not yet", because the webhook can land a
 * moment after the redirect. A paid session resolves at once; an unpaid one never will, so
 * the polling stops rather than running forever.
 *
 * **Payment unlocks the questionnaire; it doesn't produce a report.** A full report is
 * scored from the answers, so it can't exist until the paid sections have been answered.
 * The visitor is therefore sent *into* the questionnaire, not to a report. Only once a full
 * report exists does the newest-report lookup take over and send them to read it.
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
  // The visitor has a response but no report id — the checkout gave them a Stripe session
  // id, not one of ours. The newest report for the response is the full one, because
  // reports are append-only.
  //
  // `isPending` matters: a report that is *loading* is not the same as a report that is
  // *absent*, and treating the first as the second would send a visitor who has already
  // finished the paid sections back into the questionnaire to answer them again.
  const { data: report } = useLatestReport(data?.response_id ?? undefined);
  const { data: questionnaire, error: questionnaireError } = useQuestionnaire(data?.response_id ?? undefined);
  const unlock = useQuestionnaireStore((state) => state.unlock);

  // Report existence does not imply completion: each stage creates a pending record.
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
            We couldn&rsquo;t confirm your payment just now. If you were charged, it will still
            show up — email us and we&rsquo;ll check.
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

      {data?.entitled && (
        <p className="mt-4 text-muted">
          Taking you back to your questionnaire…
        </p>
      )}
    </Layout>
  );
}
