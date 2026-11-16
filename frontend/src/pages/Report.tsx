import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useMe, useQuestionnaire, useReport } from "../api/queries";
import type { ScoredLabel, ScoredProfile, CompassMatch } from "../api/types";
import Layout from "../components/Layout";
import CareerCard from "../components/report/CareerCard";
import PaywallTeaser from "../components/report/PaywallTeaser";
import TraitRadar from "../components/report/TraitRadar";
import TraitBars from "../components/report/TraitBars";
import { SECTION_ORDER, SECTION_META } from "../lib/sections";
import { formatPercent } from "../lib/format";

export default function Report() {
  const { reportId } = useParams<{ reportId: string }>();
  const { data, isPending, error, refetch } = useReport(reportId);
  const {
    data: questionnaire,
    error: questionnaireError,
    refetch: reloadQuestionnaire,
  } = useQuestionnaire(data?.response_id);
  const { data: me } = useMe();

  if (isPending) {
    return (
      <Layout>
        <p className="py-12 text-center text-muted">Loading your report…</p>
      </Layout>
    );
  }
  if (error || !data) {
    return (
      <Layout>
        <h1 className="text-2xl font-bold">We couldn’t load your report</h1>
        <button type="button" onClick={() => refetch()} className="primary-button mt-5">
          Try again
        </button>
        <Link to="/" className="ml-5 underline">
          Home
        </Link>
      </Layout>
    );
  }

  const available = new Set(questionnaire?.sections ?? []);
  const completed = new Set(questionnaire?.completed_sections ?? []);
  const locked = SECTION_ORDER.filter((section) => !available.has(section));
  const next = SECTION_ORDER.find((section) => available.has(section) && !completed.has(section));
  const report = data.payload;
  const firstName = data.display_name?.split(/\s+/)[0];
  const subjects = [
    ...new Set(report?.careers.slice(0, 3).flatMap((career) => career.subjects ?? [])),
  ];
  const activities = [
    ...new Set(report?.careers.slice(0, 3).flatMap((career) => career.activities ?? [])),
  ];

  return (
    <Layout width="wide">
      <header className="mx-auto max-w-3xl text-center">
        <p className="inline-block rounded-full bg-warm px-4 py-2 text-xs font-semibold">
          {data.stage === "eq"
            ? "Your Compass report"
            : `Your ${SECTION_META[data.stage].label.toLowerCase()} report`}
          {data.tier === "free" ? " · Free" : ""}
        </p>
        {report ? (
          <>
            <p className="mt-7 text-xl">
              {firstName ? `${firstName}, here’s what stands out.` : "Here’s what stands out."}
            </p>
            <h1 className="gradient-text mt-3 text-4xl font-extrabold leading-tight sm:text-5xl">
              {report.headline ?? "Your career profile"}
            </h1>
            <p className="mt-4 text-sm text-muted">
              Your Holland code:{" "}
              <strong className="text-ink">{report.holland_code.join("")}</strong>
            </p>
            <p className="mx-auto mt-5 max-w-xl leading-relaxed text-muted">{report.summary}</p>
          </>
        ) : (
          <>
            <h1 className="mt-6 text-4xl font-bold">Answers saved</h1>
            <p className="mt-4 text-muted">
              This earlier report has no published result yet. Your saved answers remain available.
            </p>
          </>
        )}
      </header>

      {report && (
        <>
          <div className="mt-10 grid gap-5 md:grid-cols-2">
            <section className="rounded-2xl border border-line bg-surface p-6 shadow-card">
              <h2 className="text-xl font-bold">Your six-trait profile</h2>
              <p className="mt-2 text-sm text-muted">A picture of the interests you described.</p>
              <div className="mx-auto mt-6 max-w-xs">
                <TraitRadar traits={report.traits} code={report.holland_code} />
              </div>
            </section>
            <section className="rounded-2xl border border-line bg-surface p-6 shadow-card">
              <h2 className="text-xl font-bold">Your top abilities</h2>
              <ScoreList scores={report.abilities ?? []} />
              {!!report.values?.length && (
                <>
                  <h3 className="mt-7 text-xs font-bold uppercase tracking-wide text-muted">
                    What matters most to you
                  </h3>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {report.values.map((value) => (
                      <span key={value.name} className="rounded-full bg-warm px-3 py-1 text-sm">
                        {value.name}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </section>
          </div>
          <section className="mt-6 rounded-2xl border border-line bg-surface p-6">
            <TraitBars traits={report.traits} code={report.holland_code} />
          </section>
          {!!report.trait_profiles?.length && (
            <section className="mt-6 grid gap-5 md:grid-cols-2">
              {report.trait_profiles.map((trait) => (
                <article key={trait.name} className="rounded-2xl border border-line bg-surface p-6">
                  <h2 className="text-xl font-bold">
                    {trait.name} · {formatPercent(trait.score)}
                  </h2>
                  <p className="mt-3 text-sm text-muted">{trait.headline}</p>
                  <h3 className="mt-5 font-semibold">Strengths</h3>
                  <ul className="mt-2 space-y-2 text-sm text-muted">
                    {trait.strengths.map((strength) => (
                      <li key={strength}>→ {strength}</li>
                    ))}
                  </ul>
                  <h3 className="mt-5 font-semibold">Where you might thrive</h3>
                  <p className="mt-2 text-sm text-muted">{trait.environments}</p>
                  <h3 className="mt-5 font-semibold">Example career paths</h3>
                  <p className="mt-2 text-sm text-muted">{trait.example_work}</p>
                </article>
              ))}
            </section>
          )}
          {!!report.motivators?.length && (
            <section className="mt-8 rounded-2xl border border-line bg-surface p-7">
              <h2 className="text-xl font-bold">What motivates you</h2>
              <ScoreList scores={report.motivators} />
            </section>
          )}
          {!!report.work_environment?.length && (
            <section className="mt-8 rounded-2xl border border-line bg-surface p-7">
              <h2 className="text-xl font-bold">Your preferred environment</h2>
              <ul className="mt-5 space-y-5">
                {report.work_environment.map((axis) => (
                  <li key={axis.name}>
                    <div className="flex justify-between gap-4 text-xs text-muted">
                      <span>{axis.low}</span>
                      <span>{axis.high}</span>
                    </div>
                    <div className="relative mt-3 h-2 rounded-full bg-line">
                      <span
                        className="absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent"
                        style={{ left: `${(axis.score + 100) / 2}%` }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {report.strength_profile && (
            <Profile profile={report.strength_profile} title="Your strengths & work style" />
          )}
          {report.eq_profile && (
            <Profile profile={report.eq_profile} title="Your emotional intelligence" />
          )}
          {report.compass && (
            <section className="compass-banner mt-10 rounded-3xl p-7 text-white">
              <h2 className="text-2xl font-bold">Your combined Compass</h2>
              <p className="mt-3 text-sm text-white/90">
                Overall signal agreement: <strong>{report.compass.confidence}</strong>. Interests
                contribute 40%, strengths 30%, and EQ 30%. Agreement is not a probability of career
                success.
              </p>
              <div className="mt-6 space-y-3">
                {report.compass.top_matches.map((match) => (
                  <div key={match.career_id} className="rounded-xl border border-white/25 p-4">
                    <p className="font-semibold">
                      {match.name} · {formatPercent(match.fit_score)}
                    </p>
                    <p className="mt-2 text-xs">
                      Interests {formatPercent(match.signals.interests)} · Strengths{" "}
                      {formatPercent(match.signals.strengths)} · EQ{" "}
                      {formatPercent(match.signals.eq)} · {match.confidence} agreement
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}
          <section className="mt-12">
            <div className="flex items-center justify-between gap-4">
              <h2 className="text-2xl font-bold">Your top career matches</h2>
              <span className="text-sm text-muted">{report.careers.length} matches</span>
            </div>
            <p className="mt-3 text-sm text-muted">
              {data.stage === "interests"
                ? "Based on your interests and self-reported abilities."
                : data.stage === "strengths"
                  ? "Based on your work style, environment preferences, and motivators. Your interest profile is retained above."
                  : "Ranked using all three assessments together."}{" "}
              Fit scores compare your answers with our reference profiles.
            </p>
            <ol className="mt-6 space-y-4">
              {report.careers.map((career) => (
                <CareerCard key={career.career_id ?? career.name} career={career} />
              ))}
            </ol>
          </section>
          {!!report.compass?.hidden_gems.length && (
            <Matches
              title="Hidden gems"
              description="Paths outside your top three interest matches that fit the combined profile."
              matches={report.compass.hidden_gems}
            />
          )}
          {!!report.compass?.watch_outs.length && (
            <Matches
              title="Where the signals differ"
              description="These paths score differently across the assessments. Explore what that means for you."
              matches={report.compass.watch_outs}
            />
          )}
        </>
      )}

      {questionnaireError && (
        <p role="alert" className="mt-8">
          Could not check your access.{" "}
          <button type="button" onClick={() => reloadQuestionnaire()} className="underline">
            Try again
          </button>
        </p>
      )}
      {questionnaire && locked.length > 0 && (
        <PaywallTeaser sections={locked} responseId={data.response_id} />
      )}
      {questionnaire && next && (
        <section className="mt-10 rounded-2xl border border-line bg-surface p-7">
          <h2 className="text-2xl font-bold">Next: {SECTION_META[next].label}</h2>
          <p className="mt-3 text-muted">
            Your completed stage is saved. Add another lens to your report.
          </p>
          <Link
            to={`/questionnaire?response_id=${data.response_id}`}
            className="primary-button mt-5"
          >
            Continue to {SECTION_META[next].label} →
          </Link>
        </section>
      )}
      {report && (
        <section className="mt-12">
          <h2 className="text-2xl font-bold">Your next moves</h2>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <div className="rounded-2xl border border-line bg-surface p-6">
              <h3 className="font-bold">Subjects to explore</h3>
              <p className="mt-3 text-sm leading-loose text-muted">{subjects.join(" · ")}</p>
            </div>
            <div className="rounded-2xl border border-line bg-surface p-6">
              <h3 className="font-bold">Try something small</h3>
              <ul className="mt-3 space-y-3 text-sm text-muted">
                {activities.map((activity) => (
                  <li key={activity}>→ {activity}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      )}
      <footer className="mt-12 border-t border-line pt-6 text-center text-sm text-muted">
        <p>
          A starting point, not a verdict. Your interests and skills can change as you try new
          things.
        </p>
        <p className="mt-3">
          Saved {new Date(data.generated_at ?? data.created_at).toLocaleDateString()}.
        </p>
        {me?.signed_in ? (
          <Link to="/reports" className="mt-3 block underline">
            View your report history
          </Link>
        ) : (
          <p className="mt-3">
            <a href={api.googleLoginUrl} className="underline">
              Sign in
            </a>{" "}
            to keep access across devices. Guest access depends on this browser’s session.
          </p>
        )}
        <Link to="/start" className="mt-5 inline-block text-accent underline">
          Take it again
        </Link>
        <p className="mt-2 text-xs">
          A new attempt starts free; paid access belongs to the original attempt.
        </p>
      </footer>
    </Layout>
  );
}

function ScoreList({ scores }: { scores: ScoredLabel[] }) {
  return (
    <ul className="mt-5 space-y-5">
      {scores.map((item) => (
        <li key={item.name}>
          <div className="flex justify-between gap-3 text-sm font-semibold">
            <span>{item.name}</span>
            <span>{formatPercent(item.score)}</span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-line">
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${item.score * 100}%` }}
            />
          </div>
          <p className="mt-2 text-xs text-muted">{item.description}</p>
        </li>
      ))}
    </ul>
  );
}

function Profile({ profile, title }: { profile: ScoredProfile; title: string }) {
  return (
    <section className="mt-8 rounded-2xl border border-line bg-surface p-7">
      <p className="text-xs font-bold uppercase text-accent">{title}</p>
      <h2 className="mt-3 text-2xl font-bold">{profile.name}</h2>
      <p className="mt-3 text-sm leading-relaxed text-muted">{profile.description}</p>
      <ScoreList scores={profile.scores} />
    </section>
  );
}

function Matches({
  title,
  description,
  matches,
}: {
  title: string;
  description: string;
  matches: CompassMatch[];
}) {
  return (
    <section className="mt-10">
      <h2 className="text-2xl font-bold">{title}</h2>
      <p className="mt-3 text-sm text-muted">{description}</p>
      <ul className="mt-4 space-y-3">
        {matches.map((match) => (
          <li key={match.career_id} className="rounded-xl border border-line bg-surface p-5">
            <strong>{match.name}</strong>
            <p className="mt-2 text-sm text-muted">
              {formatPercent(match.fit_score)} combined fit · {match.confidence} agreement
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
