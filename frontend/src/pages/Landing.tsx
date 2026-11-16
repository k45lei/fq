import { Link, useSearchParams } from "react-router-dom";
import { usePricing, useQuestionnaire } from "../api/queries";
import Layout from "../components/Layout";
import { useQuestionnaireStore } from "../store/questionnaire";
import { formatPrice } from "../components/report/PaywallTeaser";

export default function Landing() {
  const [params] = useSearchParams();
  const { data: questionnaire } = useQuestionnaire();
  const { data: pricing } = usePricing();
  const { responseId, reportId } = useQuestionnaireStore();
  const count = questionnaire?.questions.length ?? 32;
  const cards = [
    {
      title: "Interests",
      count,
      description:
        "Explore six career types, your abilities, work style, and values. Get eight career ideas to explore.",
    },
    {
      title: "Strengths",
      count: 76,
      description:
        "Discover your work-style archetype, motivators, and the environments where you feel at home.",
    },
    {
      title: "EQ",
      count: 30,
      description:
        "Explore how you handle emotions, connect with others, and respond to pressure across five domains.",
    },
  ];

  return (
    <Layout width="wide">
      {params.get("signin") === "failed" && (
        <p role="alert" className="mb-6 rounded-xl bg-warm p-4">
          Sign-in did not complete. You can try again or continue as a guest.
        </p>
      )}
      <section className="mx-auto max-w-3xl py-6 text-center sm:py-12">
        <p className="inline-block rounded-full bg-warm px-4 py-2 text-xs font-semibold">
          Career discovery · Three assessments · Your Compass report
        </p>
        <h1 className="mt-7 text-5xl font-extrabold leading-[1.08] tracking-tight sm:text-6xl">
          Find the career
          <br />
          <span className="gradient-text">that fits who you are.</span>
        </h1>
        <p className="mx-auto mt-6 max-w-xl leading-relaxed text-muted">
          Your interests are a starting point. Add your strengths and emotional intelligence to
          explore career paths through three different lenses.
        </p>
        <Link to="/before-quiz" className="primary-button mt-8">
          Start free →
        </Link>
        <p className="mt-4 text-xs text-muted">
          {count} questions · About {Math.ceil(count / 4)} minutes · No account needed
        </p>
        {pricing && (
          <p className="mt-2 text-xs text-muted">
            Full bundle {formatPrice(pricing.amount_cents, pricing.currency)} · One payment per
            attempt
          </p>
        )}
        {responseId && (
          <Link
            to={reportId ? `/report/${reportId}` : "/questionnaire"}
            className="mt-5 block text-sm text-accent underline"
          >
            {reportId ? "View your saved report" : "Continue your questionnaire"}
          </Link>
        )}
      </section>
      <section className="mt-8">
        <h2 className="text-center text-2xl font-bold">Three lenses. A clearer picture.</h2>
        <div className="mt-6 grid gap-4 md:grid-cols-3">
          {cards.map((card, index) => (
            <article
              key={card.title}
              className="rounded-2xl border border-line bg-surface p-6 shadow-card"
            >
              <div className="flex items-center justify-between">
                <span className="rounded-full bg-accent-soft px-3 py-1 text-accent">
                  {index + 1}
                </span>
                {index === 0 && (
                  <span className="rounded bg-warm px-2 py-1 text-xs font-bold">FREE</span>
                )}
              </div>
              <h3 className="mt-5 text-xl font-bold">{card.title}</h3>
              <p className="mt-1 text-xs text-muted">
                {card.count} questions · About {Math.ceil(card.count / 4)} minutes
              </p>
              <p className="mt-4 text-sm leading-relaxed text-muted">{card.description}</p>
            </article>
          ))}
        </div>
      </section>
      <section className="compass-banner mt-10 rounded-3xl px-7 py-12 text-center text-white">
        <p className="text-sm font-semibold">The Compass Report</p>
        <h2 className="mt-3 text-3xl font-extrabold">All three lenses. One ranked list.</h2>
        <p className="mx-auto mt-4 max-w-xl text-white/90">
          Explore five leading matches, hidden gems, and places where the signals disagree. Every
          result comes from your answers and a consistent set of scoring rules.
        </p>
        <p className="mx-auto mt-5 max-w-lg text-sm text-white/85">
          Confidence describes agreement between the three assessments. It is not a prediction of
          career success.
        </p>
      </section>
      <div className="mt-10 text-center">
        <Link to="/before-quiz" className="primary-button">
          Start with the free interests quiz →
        </Link>
        <p className="mt-4 text-xs text-muted">
          Drafts stay in your browser. Completed answers and reports are saved securely.
        </p>
      </div>
    </Layout>
  );
}
