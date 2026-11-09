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

import { useCheckoutStatus, useLatestReport } from "../api/queries";
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
  const { data: report, isPending: reportPending } = useLatestReport(data?.response_id ?? undefined);
  const unlock = useQuestionnaireStore((state) => state.unlock);

  // A full report exists only once the paid sections have been answered. If one is there,
  // it's the point — the visitor came here to read it.
  useEffect(() => {
    if (report?.tier === "full") {
      navigate(`/report/${report.id}`, { replace: true });
    }
  }, [report, navigate]);

  // Otherwise, the paid sections are still to do. Send the visitor into the questionnaire
  // rather than leaving them on a page that says "thank you" and stops.
  //
  // `unlock` clears the stored report id — it's the free one, and leaving it set would
  // bounce the questionnaire straight back to it — and the position, so the paid sections
  // start at their beginning. The draft answers are kept: they're the visitor's work.
  useEffect(() => {
    if (data?.entitled && !reportPending && !report && data.response_id) {
      unlock(data.response_id);
      navigate("/questionnaire", { replace: true });
    }
  }, [data, report, reportPending, unlock, navigate]);

  return (
    <Layout>
      <h1 className="font-display text-4xl font-semibold">Thank you</h1>

      {isPending && <p className="mt-4 text-muted">Confirming your payment…</p>}

      {error && (
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
            Your payment went through, and we&rsquo;re still turning it into access. This
            usually takes a moment. You can close this tab — the report will be in your
            account when it&rsquo;s ready.
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
          Taking you to the rest of the questionnaire — the full report is scored from your
          answers…
        </p>
      )}
    </Layout>
  );
}
