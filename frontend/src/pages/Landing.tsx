import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useQuestionnaire } from "../api/queries";
import Layout from "../components/Layout";
import { SECTION_META, SECTION_ORDER } from "../lib/sections";
import { useQuestionnaireStore } from "../store/questionnaire";

const STEPS = [
  {
    title: "Answer honestly",
    detail: "No right answers, nothing timed.",
  },
  {
    title: "Save your answers",
    detail: "Your completed sections are saved securely.",
  },
  {
    title: "Results are coming soon",
    detail: "Personalized guidance is not available yet.",
  },
];

export default function Landing() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { data: questionnaire } = useQuestionnaire();
  const { responseId, reportId } = useQuestionnaireStore();

  const questionCount = questionnaire?.questions.length ?? 0;
  // Roughly fifteen seconds a question, rounded to something a person would say out loud.
  const minutes = questionCount > 0 ? Math.max(2, Math.round((questionCount * 15) / 60)) : null;

  // What the server will actually serve, versus everything the product offers. The gap is the
  // paywall: the server decides which sections this caller may answer, and the teaser only
  // ever names the ones it withheld.
  const available = new Set(questionnaire?.sections ?? []);
  const locked = SECTION_ORDER.filter((section) => !available.has(section));

  return (
    <Layout width="wide">
      {/* The OAuth callback redirects here on failure rather than returning JSON — the browser
          arrives as a navigation, so a JSON error body would render as text at a dead end. */}
      {searchParams.get("signin") === "failed" && (
        <div className="mb-10 rounded-xl border border-red-200 bg-red-50 p-5">
          <p className="font-medium text-red-800">Sign-in didn&rsquo;t complete</p>
          <p className="mt-1 text-sm text-red-800/80 text-pretty">
            Either it was cancelled, or Google hasn&rsquo;t verified that address. You can
            carry on without signing in — it only affects reaching your reports from another
            device.
          </p>
        </div>
      )}

      <section className="grid items-start gap-12 lg:grid-cols-[1.15fr_1fr]">
        <div>
          <h1 className="font-display text-5xl leading-[1.08] font-semibold text-balance sm:text-6xl">
            Work out what you&rsquo;re actually suited to.
          </h1>
          {/* Prose stays near 65 characters even on a wide page — the measure is for the
              grids, not for paragraphs. */}
          <p className="mt-5 max-w-prose text-lg leading-relaxed text-muted text-pretty">
            Most careers advice asks what you want to be, or tells you what&rsquo;s hiring. This
            asks how you think — then shows you the work that suits people who think that way.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Link
              to="/start"
              className="rounded-xl bg-accent px-8 py-3.5 font-medium text-white transition hover:bg-accent-strong"
            >
              Start
            </Link>
            <span className="text-sm text-muted">
              {questionCount > 0 ? `${questionCount} questions` : "A short questionnaire"}
              {minutes ? ` · about ${minutes} min` : ""} · free
            </span>
          </div>

          {(reportId || responseId) && (
            <button
              type="button"
              onClick={() => navigate(reportId ? `/report/${reportId}` : "/questionnaire")}
              className="mt-5 block text-accent underline underline-offset-4 hover:text-accent-strong"
            >
              {reportId ? "View your previous results" : "Pick up where you left off"}
            </button>
          )}
        </div>

        <div className="rounded-2xl border border-line bg-surface p-6 shadow-card">
          <h2 className="text-sm font-medium tracking-wide text-muted uppercase">How it works</h2>
          <ol className="mt-4 space-y-4">
            {STEPS.map((step, index) => (
              <li key={step.title} className="flex gap-4">
                <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-accent text-xs font-medium text-white">
                  {index + 1}
                </span>
                <div>
                  <p className="font-medium">{step.title}</p>
                  <p className="text-sm text-muted">{step.detail}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mt-16">
        <h2 className="font-display text-3xl font-semibold">What we ask about</h2>
        {/* Every section, always — including the ones behind the paywall. Hiding them made the
            product look like it was one third of its actual size. */}
        <div className="mt-6 grid gap-4 lg:grid-cols-3">
          {SECTION_ORDER.map((section, index) => {
            const meta = SECTION_META[section];
            const included = available.has(section);

            return (
              <div
                key={section}
                className={`flex flex-col rounded-2xl border p-6 ${
                  included
                    ? "border-line bg-surface shadow-card"
                    : "border-dashed border-accent/40 bg-warm"
                }`}
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs font-medium tracking-wide text-muted uppercase">
                    Part {index + 1}
                  </span>
                  {/* Locked sections keep full-contrast text. The point is to sell them, not
                      to grey them out — a disabled-looking card reads as broken. */}
                  {!included && (
                    <span className="rounded-md bg-accent px-2 py-0.5 text-xs font-medium text-white">
                      Paid
                    </span>
                  )}
                </div>

                <h3 className="mt-2 font-display text-2xl font-semibold">{meta.label}</h3>
                <p className="mt-1 font-medium text-pretty">{meta.blurb}</p>
                <p className="mt-3 text-sm leading-relaxed text-muted text-pretty">
                  {meta.detail}
                </p>

                <ul className="mt-4 space-y-1.5 border-t border-line pt-4 text-sm text-muted">
                  {meta.asks.map((ask) => (
                    <li key={ask} className="flex gap-2.5">
                      <span aria-hidden className="text-accent">
                        &middot;
                      </span>
                      <span className="text-pretty">{ask}</span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>

        {locked.length > 0 && (
          <p className="mt-5 text-sm text-muted text-pretty">
            Start with {SECTION_META[SECTION_ORDER[0]!].label.toLowerCase()} — it&rsquo;s free
            to complete. Payment unlocks the remaining sections. Personalized results are coming soon.
          </p>
        )}
      </section>
    </Layout>
  );
}
