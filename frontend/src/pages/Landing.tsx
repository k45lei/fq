import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useQuestionnaire } from "../api/queries";
import Layout from "../components/Layout";
import WireSphere from "../components/WireSphere";
import { SECTION_META, SECTION_ORDER } from "../lib/sections";
import { useQuestionnaireStore } from "../store/questionnaire";

const STEPS = [
  {
    title: "Answer honestly",
    detail: "No right answers, nothing timed.",
  },
  {
    title: "We work out your profile",
    detail: "Scored against six established career traits.",
  },
  {
    title: "You get something to act on",
    detail: "Fields, roles, and concrete next steps.",
  },
];

export default function Landing() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { data: questionnaire } = useQuestionnaire();
  const { responseId, reportId } = useQuestionnaireStore();

  const questionCount = questionnaire?.questions.length ?? 0;
  const minutes = questionCount > 0 ? Math.max(2, Math.round((questionCount * 15) / 60)) : null;

  const available = new Set(questionnaire?.sections ?? []);
  const locked = SECTION_ORDER.filter((section) => !available.has(section));

  return (
    <Layout width="wide">
      {searchParams.get("signin") === "failed" && (
        <div className="mb-10 rounded-xl border border-line bg-warm p-5">
          <p className="font-medium text-ink">Sign-in didn&rsquo;t complete</p>
          <p className="mt-1 text-sm text-muted text-pretty">
            Either it was cancelled, or Google hasn&rsquo;t verified that address. You can
            carry on without signing in — it only affects reaching your reports from another
            device.
          </p>
        </div>
      )}

      <section className="grid items-center gap-12 lg:grid-cols-[1.15fr_1fr]">
        <div className="flex flex-col gap-12">
          <div>
            <h1 className="font-display text-5xl leading-[1.08] font-semibold text-balance sm:text-6xl">
              Work out what you&rsquo;re actually suited to.
            </h1>
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

          <div className="rounded-2xl border border-line bg-surface p-6 shadow-card max-w-prose">
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
        </div>

        <div className="h-[400px] w-full">
          <WireSphere />
        </div>
      </section>

      <section className="mt-16">
        <h2 className="font-display text-3xl font-semibold">What we ask about</h2>
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
            and gives you a complete report on its own. The paid{" "}
            {locked.length === 1 ? "section sharpens" : "sections sharpen"} it afterwards.
          </p>
        )}
      </section>
    </Layout>
  );
}
