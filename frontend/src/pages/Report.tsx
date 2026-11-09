import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
// No runtime import needed; ReportTier is a TypeScript type only.

import { useMe, useQuestionnaire, useReport } from "../api/queries";
import type { QuestionCategory } from "../api/types";
import Layout from "../components/Layout";
import CareerCard from "../components/report/CareerCard";
import PaywallTeaser from "../components/report/PaywallTeaser";
import TraitBars from "../components/report/TraitBars";
import TraitRadar from "../components/report/TraitRadar";
import { formatPercent } from "../lib/format";
import { SECTION_META, SECTION_ORDER } from "../lib/sections";

export default function Report() {
  const { reportId } = useParams<{ reportId: string }>();
  const { data, isPending, error } = useReport(reportId);
  // The questionnaire is asked about *this* response, because the entitlement gate looks
  // entitlements up by user and response: a `single_report` names one response, so a preview
  // with no response to ask about can't widen.
  const { data: questionnaire } = useQuestionnaire(data?.response_id);
  const { data: me } = useMe();

  // Two different sets, and the difference matters.
  //
  // `locked` is what the server won't let this visitor answer — the paywall. `pending` is
  // what they *may* answer but haven't yet — which for someone who has just paid is the
  // whole point of coming back.
  //
  // Only offer what the reader hasn't already answered. The teaser names only the sections
  // the server withheld, so it disappears rather than selling them what they just did.
  const available = new Set(questionnaire?.sections ?? SECTION_ORDER);
  const completed = new Set(questionnaire?.completed_sections ?? []);
  const locked = SECTION_ORDER.filter((section) => !available.has(section));
  const pending = SECTION_ORDER.filter(
    (section) => available.has(section) && !completed.has(section),
  );

  if (isPending) {
    return (
      <Layout width="wide">
        <p className="py-16 text-center text-muted">Building your results…</p>
      </Layout>
    );
  }

  if (error || !data) {
    return (
      <Layout width="wide">
        <div className="py-16 text-center">
          <p className="text-muted">We couldn&rsquo;t find that report.</p>
          <Link to="/" className="mt-3 inline-block text-accent underline underline-offset-4">
            Start again
          </Link>
        </div>
      </Layout>
    );
  }

  const report = data.payload;
  const familyNames = new Map(report.families.map((family) => [family.family_id, family.name]));
  const codeLabels = report.holland_code.map((trait) => report.traits[trait]?.label ?? trait);

  return (
    <Layout width="wide">
      <header>
        <p className="text-sm tracking-wide text-muted uppercase">Your profile</p>
        <h1 className="mt-3 font-display text-5xl leading-[1.12] font-semibold text-balance sm:text-6xl">
          {codeLabels.length > 0 ? codeLabels.join(", ") : "Your results"}
        </h1>
        <p className="mt-5 max-w-xl text-lg leading-relaxed text-muted text-pretty">
          These three came through most strongly in your answers. Everything below follows from
          them.
        </p>

        <a
          href={api.reportPdfUrl(data.id)}
          className="mt-6 inline-block rounded-xl border border-line bg-surface px-5 py-2.5 text-sm font-medium shadow-card transition hover:border-accent/50"
        >
          Download as PDF
        </a>
      </header>

      <Section title="How you scored">
        {/* Shape on one side, numbers on the other. The radar is the gestalt — it shows which
            way a profile leans — and the bars are what you read values off. */}
        <div className="grid items-start gap-10 sm:grid-cols-[280px_1fr]">
          <TraitRadar traits={report.traits} code={report.holland_code} />
          <TraitBars traits={report.traits} code={report.holland_code} />
        </div>
      </Section>

      {report.strengths.length > 0 && (
        <Section title="What you're bringing">
          {/* One per trait in the Holland code, so three across divides evenly — no empty
              corner, and more compact than stacking them. */}
          <ul className="grid gap-3 sm:grid-cols-3">
            {report.strengths.map((strength) => (
              <li
                key={strength.name}
                className="rounded-2xl border border-line bg-surface p-5 shadow-card"
              >
                <h3 className="font-medium">{strength.name}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted text-pretty">
                  {strength.evidence}
                </p>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {data.tier === "full" && report.families.length > 0 && (
        <Section
          title="Fields that fit"
          subtitle="Ranked by how closely each one matches your profile."
        >
          <ol className="overflow-hidden rounded-2xl border border-line bg-surface shadow-card">
            {report.families.map((family, position) => (
              <li
                key={family.family_id}
                className={`flex items-center gap-4 px-5 py-4 ${
                  position > 0 ? "border-t border-line" : ""
                }`}
              >
                <span className="w-5 shrink-0 font-display text-lg font-semibold tabular-nums">
                  {family.rank}
                </span>
                <span className="flex-1 font-medium">{family.name}</span>
                <span className="w-28 shrink-0">
                  <span className="block h-1 overflow-hidden rounded-full bg-line">
                    <span
                      className="block h-full rounded-full bg-accent"
                      style={{ width: `${Math.max(family.fit_score * 100, 2)}%` }}
                    />
                  </span>
                </span>
                <span className="w-10 shrink-0 text-right text-sm tabular-nums text-muted">
                  {formatPercent(family.fit_score)}
                </span>
              </li>
            ))}
          </ol>
        </Section>
      )}

      {data.tier === "full" && report.careers.length > 0 && (
        <Section
          title="Roles worth looking at"
          subtitle="A starting point, not a shortlist — each with something you could do about it."
        >
          <ul className="grid gap-3 sm:grid-cols-2">
            {report.careers.map((career) => (
              <CareerCard
                key={`${career.family_id}-${career.name}`}
                career={career}
                familyName={familyNames.get(career.family_id)}
              />
            ))}
          </ul>
        </Section>
      )}

      {data.tier === "full" && report.challenges.length > 0 && (
        <Section
          title="Things to watch"
          subtitle="Every strength has a flip side. These are the ones your answers point at."
        >
          <ul className="space-y-5">
            {report.challenges.map((challenge) => (
              <li key={challenge.title} className="border-l-2 border-line pl-5">
                <h3 className="font-medium">{challenge.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{challenge.detail}</p>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {locked.length > 0 ? (
        <PaywallTeaser sections={locked} responseId={data.response_id} />
      ) : pending.length > 0 ? (
        <ContinuePrompt sections={pending} responseId={data.response_id} />
      ) : null}

      <footer className="mt-10 text-sm text-muted">
        <p>Generated {new Date(data.generated_at).toLocaleDateString()}.</p>
        {/* The prompt only appears where it's useful: once signed in, the report is already
            reachable from anywhere and saying so again is noise. */}
        {me?.signed_in ? (
          <p className="mt-1">
            Saved to your account — find it again under{" "}
            <Link to="/reports" className="underline underline-offset-4 hover:text-ink">
              your reports
            </Link>
            .
          </p>
        ) : (
          <p className="mt-1">
            This link works on this device.{" "}
            <a
              href={api.googleLoginUrl}
              className="underline underline-offset-4 hover:text-ink"
            >
              Sign in
            </a>{" "}
            to reach it from anywhere.
          </p>
        )}
      </footer>
    </Layout>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-14 border-t border-line pt-10">
      <h2 className="font-display text-3xl font-semibold">{title}</h2>
      {subtitle && <p className="mt-1.5 text-muted text-pretty">{subtitle}</p>}
      <div className="mt-6">{children}</div>
    </section>
  );
}

/**
 * Shown to someone who has paid but hasn't answered the paid sections yet.
 *
 * The same question as the paywall, asked the other way round: not "would you like the rest"
 * but "you bought the rest — here it is". Without this, a visitor who has just paid sees
 * the report they already had and no indication that the purchase unlocked anything they
 * haven't done.
 */
function ContinuePrompt({
  sections,
  responseId,
}: {
  sections: QuestionCategory[];
  responseId: string;
}) {
  return (
    <section className="mt-14 rounded-2xl border border-line bg-warm p-7">
      <h2 className="font-display text-3xl font-semibold">There&rsquo;s more to answer</h2>
      <p className="mt-2 text-sm text-muted text-pretty">
        You&rsquo;ve unlocked the rest of the questionnaire.{" "}
        {sections.length === 1 ? "One section is" : `${sections.length} sections are`} still to
        do, and the full report is scored from all of them.
      </p>

      <ul className="mt-5 space-y-3">
        {sections.map((section) => (
          <li key={section} className="flex gap-3">
            <span aria-hidden className="mt-0.5 text-muted">
              &#8226;
            </span>
            <div>
              <p className="font-medium">{SECTION_META[section].label}</p>
              <p className="mt-0.5 text-sm text-muted text-pretty">
                {SECTION_META[section].blurb}
              </p>
            </div>
          </li>
        ))}
      </ul>

      <Link
        to={`/questionnaire?response_id=${responseId}`}
        className="mt-7 inline-block rounded-xl bg-accent px-6 py-3 font-medium text-white transition hover:bg-accent-strong"
      >
        Continue the questionnaire
      </Link>
    </section>
  );
}
