import { Link } from "react-router-dom";
import Layout from "../components/Layout";

export default function BeforeQuiz() {
  return (
    <Layout variant="before_quiz">
      <section className="hero">
        <div className="eyebrow-pill">
          <span className="eyebrow-dot"></span>
          <span>Free quiz · Preview your match</span>
        </div>

        <h1>
          What career fits
          <br />
          <span className="hero-gradient-text">the real you?</span>
        </h1>

        <p className="hero-subtitle">
          Answer 32 honest questions and find out. We'll match you with careers that fit your
          interests, abilities, and what you actually care about — not just what looks good on
          paper.
        </p>

        <div className="hero-actions">
          <Link to="/start" className="btn-primary">
            Start the quiz <span>→</span>
          </Link>
          <span className="hero-duration">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
            Takes about 8 minutes
          </span>
        </div>
      </section>

      <section className="features-section">
        <div className="cards-grid">
          <article className="feature-card">
            <div className="icon-circle">
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
            </div>
            <h2>Discover your code</h2>
            <p>
              Find which of the 6 career types fits you best — Builder, Thinker, Creator, Helper,
              Leader, or Organizer.
            </p>
          </article>

          <article className="feature-card">
            <div className="icon-circle">
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
            </div>
            <h2>See your strengths</h2>
            <p>
              A clear breakdown of your top abilities, work style, and what you actually value in a
              job.
            </p>
          </article>

          <article className="feature-card">
            <div className="icon-circle">
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
            </div>
            <h2>Get real career ideas</h2>
            <p>
              Match with 8 careers that fit who you are — with subjects and activities to start
              exploring now.
            </p>
          </article>
        </div>
      </section>

      <section className="how-it-works-wrapper">
        <div className="how-card">
          <h2>How it works</h2>

          <div className="steps-grid">
            <div>
              <div className="step-number">01</div>
              <div className="step-title">Interests</div>
              <div className="step-desc">What naturally grabs you</div>
            </div>

            <div>
              <div className="step-number">02</div>
              <div className="step-title">Abilities</div>
              <div className="step-desc">Where you already shine</div>
            </div>

            <div>
              <div className="step-number">03</div>
              <div className="step-title">Work style</div>
              <div className="step-desc">How you like to work</div>
            </div>

            <div>
              <div className="step-number">04</div>
              <div className="step-title">Values</div>
              <div className="step-desc">What makes a job worth doing</div>
            </div>
          </div>

          <p className="how-footer-note">
            Drafts stay in this browser. When you submit a complete stage, your answers and report
            are saved. Sign in to access your results on another device.
          </p>
        </div>
      </section>
    </Layout>
  );
}
