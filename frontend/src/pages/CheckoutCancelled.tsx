/**
 * The return from Stripe when the visitor backed out.
 *
 * Nothing was paid, so nothing is granted and nothing needs reconciling. The only job is to
 * not make the visitor feel they did something wrong — they didn't complete a purchase, and
 * the free report they already have is still theirs.
 *
 * The response id is in the cancel URL (the server builds it from the configured origin), so
 * the visitor can go straight back to the report they were reading. If it's missing, the
 * start page is a reasonable fallback.
 */

import { Link, useSearchParams } from "react-router-dom";

import Layout from "../components/Layout";

export default function CheckoutCancelled() {
  const [params] = useSearchParams();
  const responseId = params.get("response_id");

  return (
    <Layout>
      <h1 className="font-display text-4xl font-semibold">No charge was made</h1>
      <p className="mt-4 text-muted text-pretty">
        You can come back to this any time. The free report you&rsquo;ve already got is still
        here, and nothing was taken from your card.
      </p>

      <div className="mt-7 flex flex-wrap gap-3">
        {responseId ? (
          <Link
            to="/questionnaire"
            className="rounded-xl bg-accent px-6 py-3 font-medium text-white transition hover:bg-accent-strong"
          >
            Back to the questions
          </Link>
        ) : (
          <Link
            to="/"
            className="rounded-xl bg-accent px-6 py-3 font-medium text-white transition hover:bg-accent-strong"
          >
            Back to the start
          </Link>
        )}
      </div>
    </Layout>
  );
}
