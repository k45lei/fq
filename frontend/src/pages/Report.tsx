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
      <Layout variant="report">
        <p className="py-12 text-center text-muted">Loading your report…</p>
      </Layout>
    );
  }
  if (error || !data) {
    return (
      <Layout variant="report">
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
  const [primaryHeadline, ...secondaryHeadline] = (report?.headline ?? "Your career profile").split(
    " + ",
  );
  const subjects = [
    ...new Set(report?.careers.slice(0, 3).flatMap((career) => career.subjects ?? [])),
  ];
  const activities = [
    ...new Set(report?.careers.slice(0, 3).flatMap((career) => career.activities ?? [])),
  ];

  return (
    <Layout variant="report">
      <header className="report-hero">
        <p className="eyebrow-pill">
          {data.stage === "eq"
            ? "Your Compass report"
            : `Your ${SECTION_META[data.stage].label.toLowerCase()} report`}
          {data.tier === "free" ? " · Free" : ""}
        </p>
        {report ? (
          <>
            <p className="hero-kicker">
              {firstName ? `${firstName}, here’s what stands out.` : "Here’s what stands out."}
            </p>
            <h1>
              {primaryHeadline}
              {secondaryHeadline.length > 0 && (
                <>
                  {" "}
                  + <span className="hero-gradient-text">{secondaryHeadline.join(" + ")}</span>
                </>
              )}
            </h1>
            <p className="holland-code">
              Your Holland code:{" "}
              <strong className="code-badge">{report.holland_code.join("")}</strong>
            </p>
            <p className="hero-summary">{report.summary}</p>
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
          <div className="profile-overview-grid">
            <section className="card">
              <h2 className="card-title">Your six-trait profile</h2>
              <p className="card-subtitle">A picture of the interests you described.</p>
              <div className="radar-wrapper">
                <TraitRadar traits={report.traits} code={report.holland_code} />
              </div>
            </section>
            <section className="card">
              <h2 className="card-title">Your top abilities</h2>
              <ScoreList scores={report.abilities ?? []} />
              {!!report.values?.length && (
                <div className="values-divider">
                  <h3 className="micro-label">What matters most to you</h3>
                  <div className="value-tags">
                    {report.values.map((value) => (
                      <span key={value.name} className="value-tag">
                        {value.name}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </section>
          </div>
          {!report.trait_profiles?.length && (
            <section className="card report-extra">
              <TraitBars traits={report.traits} code={report.holland_code} />
            </section>
          )}
          {!!report.trait_profiles?.length && (
            <section className="traits-grid">
              {report.trait_profiles.map((trait) => (
                <article key={trait.name} className="card">
                  <div className="trait-header">
                    <h2 className="trait-name">{trait.name}</h2>
                    <div className="trait-score">
                      {Math.round(trait.score * 100)}
                      <span>/100</span>
                    </div>
                  </div>
                  <p className="trait-tagline">{trait.headline}</p>
                  <div className="trait-bar-track">
                    <div className="trait-bar-fill" style={{ width: `${trait.score * 100}%` }} />
                  </div>
                  <div className="trait-block">
                    <h3 className="micro-label">Strengths</h3>
                    <ul className="trait-bullets">
                      {trait.strengths.map((strength) => (
                        <li key={strength}>{strength}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="trait-block">
                    <h3 className="micro-label">Where you thrive</h3>
                    <p>{trait.environments}</p>
                  </div>
                  <div className="trait-block">
                    <h3 className="micro-label">Example career paths</h3>
                    <p>{trait.example_work}</p>
                  </div>
                </article>
              ))}
            </section>
          )}
          {!!report.motivators?.length && (
            <section className="card report-extra">
              <h2 className="card-title">What motivates you</h2>
              <ScoreList scores={report.motivators} />
            </section>
          )}
          {!!report.work_environment?.length && (
            <section className="card report-extra">
              <h2 className="card-title">Your preferred environment</h2>
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
          <section className="matches-section">
            <div className="matches-header">
              <h2 className="text-2xl font-bold">Your top career matches</h2>
              <span className="matches-count">{report.careers.length} matches</span>
            </div>
            <p className="mt-3 text-sm text-muted">
              {data.stage === "interests"
                ? "Based on your interests and self-reported abilities."
                : data.stage === "strengths"
                  ? "Based on your work style, environment preferences, and motivators. Your interest profile is retained above."
                  : "Ranked using all three assessments together."}{" "}
              Fit scores compare your answers with our reference profiles.
            </p>
            <ol className="careers-grid">
              {report.careers.map((career, index) => (
                <CareerCard
                  key={career.career_id ?? career.name}
                  career={career}
                  rank={index + 1}
                />
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
        <section className="card report-extra">
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
        <section className="card next-moves-card">
          <h2 className="card-title">Your next moves</h2>
          <p className="card-subtitle">Based on your top three matches — start small, start now.</p>
          <div className="next-moves-grid">
            <div>
              <h3 className="micro-label">Lean into these subjects</h3>
              <div className="subject-pills">
                {subjects.map((subject) => (
                  <span className="subject-pill" key={subject}>
                    {subject}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <h3 className="micro-label">Try this in the next 6 months</h3>
              <ul className="checklist">
                {activities.map((activity) => (
                  <li key={activity}>
                    <span className="check-icon" aria-hidden="true">
                      ✓
                    </span>
                    <span>{activity}</span>
                  </li>
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
      {questionnaire && locked.length > 0 && (
        <div className="sticky-bottom-bar">
          <div className="sticky-bottom-inner">
            <div>
              <div className="sticky-left-title">Next step</div>
              <div className="sticky-left-sub">
                Strengths + EQ + your combined Compass career report
              </div>
            </div>
            <a href="#unlock" className="btn-sticky-cta">
              Unlock the full picture →
            </a>
          </div>
        </div>
      )}
    </Layout>
  );
}

function ScoreList({ scores }: { scores: ScoredLabel[] }) {
  return (
    <ul className="ability-list">
      {scores.map((item) => (
        <li key={item.name} className="ability-item">
          <div className="ability-header">
            <span className="ability-name">{item.name}</span>
            <span className="ability-score">{formatPercent(item.score)}</span>
          </div>
          <div className="ability-bar-track">
            <div className="ability-bar-fill" style={{ width: `${item.score * 100}%` }} />
          </div>
          <p className="ability-desc">{item.description}</p>
        </li>
      ))}
    </ul>
  );
}

function Profile({ profile, title }: { profile: ScoredProfile; title: string }) {
  return (
    <section className="card report-extra">
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
