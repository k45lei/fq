import { Link, useSearchParams } from "react-router-dom";
import { usePricing } from "../api/queries";
import { formatPrice } from "../components/report/PaywallTeaser";
import { useQuestionnaireStore } from "../store/questionnaire";
import Layout from "../components/Layout";

export default function Landing() {
  const { data: pricing } = usePricing();
  const { responseId, reportId } = useQuestionnaireStore();
  const [params] = useSearchParams();
  return (
    <Layout variant="landing">
      {params.get("signin") === "failed" && (
        <p role="alert">Sign-in did not complete. Try again or continue as a guest.</p>
      )}

      <section className="hero">
        <div className="eyebrow-pill">
          <span>✨</span>
          <span>Career discovery for students · 3 assessments · Compass report</span>
        </div>

        <h1>
          Find the career
          <br />
          <span className="hero-gradient-text">that actually fits.</span>
        </h1>

        <p className="hero-subtitle">
          Most career quizzes ask about interests and stop there. FutureQuest looks at your{" "}
          <strong>interests</strong>, your <strong>strengths</strong>, and your{" "}
          <strong>emotional intelligence</strong> — then combines all three into one clear report
          with career ideas you can explore.
        </p>

        <Link to="/before-quiz" className="btn-primary">
          Start free — 8 min <span>→</span>
        </Link>

        <p className="hero-note">
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <circle cx="12" cy="12" r="10"></circle>
            <polyline points="12 6 12 12 16 14"></polyline>
          </svg>
          First assessment is free
          {pricing ? ` · Full bundle ${formatPrice(pricing.amount_cents, pricing.currency)}` : ""}
        </p>
        {responseId && (
          <Link className="resume-link" to={reportId ? `/report/${reportId}` : "/questionnaire"}>
            {reportId ? "View your saved report" : "Continue your questionnaire"}
          </Link>
        )}
      </section>

      <section className="lenses-section">
        <h2 className="section-heading">Three lenses. One clear answer.</h2>

        <div className="cards-grid">
          <article className="lens-card">
            <div className="card-header">
              <div className="card-icons">
                <span className="step-badge">1</span>
                <span className="card-symbol">
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <circle cx="12" cy="12" r="10"></circle>
                    <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"></polygon>
                  </svg>
                </span>
              </div>
              <span className="free-tag">FREE</span>
            </div>
            <h3>Interests</h3>
            <div className="lens-meta">8 min · Free</div>
            <p>
              What kinds of work would you actually enjoy? Map your interests across 6 career types
              and get 8 personalized matches.
            </p>
          </article>

          <article className="lens-card">
            <div className="card-header">
              <div className="card-icons">
                <span className="step-badge">2</span>
                <span className="card-symbol">
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-2.54Z"></path>
                    <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-2.54Z"></path>
                  </svg>
                </span>
              </div>
            </div>
            <h3>Strengths</h3>
            <div className="lens-meta">19 min</div>
            <p>
              What are you naturally good at? Find your work-style archetype and see careers where
              your talents shine.
            </p>
          </article>

          <article className="lens-card">
            <div className="card-header">
              <div className="card-icons">
                <span className="step-badge">3</span>
                <span className="card-symbol">
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"></path>
                  </svg>
                </span>
              </div>
            </div>
            <h3>EQ</h3>
            <div className="lens-meta">8 min</div>
            <p>
              How do you handle yourself, others, and pressure? Map your emotional intelligence
              across 5 domains.
            </p>
          </article>
        </div>
      </section>

      <section className="compass-wrapper">
        <div className="compass-banner">
          <div className="compass-pill">
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
            >
              <circle cx="12" cy="12" r="10"></circle>
              <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"></polygon>
            </svg>
            <span>The Compass Report</span>
          </div>

          <h2>All three lenses. One ranked list.</h2>

          <p className="compass-desc">
            Careers where your interests, strengths, AND EQ all line up get pushed to the top with
            high confidence. Where the signals disagree, we tell you honestly. No black-box magic —
            just a clearer picture.
          </p>

          <ul className="compass-list">
            <li>
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="9 12 11 14 15 10"></polyline>
              </svg>
              <span>Top 5 careers ranked by combined fit</span>
            </li>
            <li>
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="9 12 11 14 15 10"></polyline>
              </svg>
              <span>Confidence badge showing where all 3 lenses agree</span>
            </li>
            <li>
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="9 12 11 14 15 10"></polyline>
              </svg>
              <span>Hidden gems you might not have considered</span>
            </li>
            <li>
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="9 12 11 14 15 10"></polyline>
              </svg>
              <span>Honest watch-outs where the signals conflict</span>
            </li>
            <li>
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="9 12 11 14 15 10"></polyline>
              </svg>
              <span>Subjects and activities to start exploring</span>
            </li>
          </ul>
        </div>
      </section>

      <section className="bottom-cta">
        <Link to="/before-quiz" className="btn-dark">
          Start with the free interests quiz <span>→</span>
        </Link>
        <p className="privacy-note">No account needed · Completed answers and reports are saved</p>
      </section>
    </Layout>
  );
}
