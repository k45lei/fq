import { Link } from "react-router-dom";

import { api } from "../api/client";
import { useMe } from "../api/queries";
import Layout from "../components/Layout";

/**
 * Past reports for the signed-in account.
 *
 * This is the one thing sign-in actually buys, so the empty and signed-out states say what to
 * do rather than just reporting that there's nothing here.
 */
export default function History() {
  const { data: me, isPending } = useMe();

  if (isPending) {
    return (
      <Layout>
        <p className="py-16 text-center text-muted">Loading…</p>
      </Layout>
    );
  }

  if (!me?.signed_in) {
    return (
      <Layout>
        <h1 className="font-display text-4xl font-semibold">Your reports</h1>
        <p className="mt-4 text-muted text-pretty">
          Sign in to see reports you&rsquo;ve completed before, on this device or any other.
        </p>
        <a
          href={api.googleLoginUrl}
          className="mt-6 inline-block rounded-xl bg-accent px-6 py-3 font-medium text-white transition hover:bg-accent-strong"
        >
          Sign in with Google
        </a>
      </Layout>
    );
  }

  return (
    <Layout>
      <h1 className="font-display text-4xl font-semibold">Your reports</h1>
      <p className="mt-3 text-muted">
        Signed in as {me.email}. Reports are kept — nothing here expires.
      </p>

      {me.reports.length === 0 ? (
        <div className="mt-8 rounded-2xl border border-line bg-surface p-6 shadow-card">
          <p className="text-muted text-pretty">
            Nothing yet. Reports appear here once you&rsquo;ve finished a questionnaire.
          </p>
          <Link
            to="/start"
            className="mt-4 inline-block rounded-xl bg-accent px-6 py-3 font-medium text-white transition hover:bg-accent-strong"
          >
            Start one
          </Link>
        </div>
      ) : (
        <ul className="mt-8 overflow-hidden rounded-2xl border border-line bg-surface shadow-card">
          {me.reports.map((report, index) => (
            <li
              key={report.id}
              className={`flex items-center justify-between gap-4 px-5 py-4 ${
                index > 0 ? "border-t border-line" : ""
              }`}
            >
              <div>
                <p className="font-medium">
                  {new Date(report.generated_at).toLocaleDateString(undefined, {
                    day: "numeric",
                    month: "long",
                    year: "numeric",
                  })}
                </p>
                <p className="text-sm text-muted">
                  {report.tier === "full" ? "Full report" : "Free report"}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-4 text-sm">
                <a
                  href={api.reportPdfUrl(report.id)}
                  className="text-muted underline underline-offset-4 hover:text-ink"
                >
                  PDF
                </a>
                <Link
                  to={`/report/${report.id}`}
                  className="rounded-lg border border-line px-4 py-2 font-medium transition hover:border-accent/50"
                >
                  Open
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Layout>
  );
}
