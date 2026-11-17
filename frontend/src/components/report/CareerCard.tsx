import { formatPercent } from "../../lib/format";
import type { CareerSuggestion } from "../../api/types";

export default function CareerCard({ career, rank }: { career: CareerSuggestion; rank: number }) {
  return (
    <li className="career-card">
      <div className="career-top">
        <span className="rank-circle">{rank}</span>
        <div className="career-title-area">
          <h3>{career.name}</h3>
          <div className="match-bar-row">
            <div className="match-track">
              <div className="match-fill" style={{ width: `${career.fit_score * 100}%` }} />
            </div>
            <span className="match-label">{formatPercent(career.fit_score)} match</span>
          </div>
        </div>
      </div>
      {career.description && <p className="career-desc">{career.description}</p>}
      <div className="why-box">
        💡 <strong>Why it fits:</strong> {career.rationale}
      </div>
      <details className="career-details">
        <summary className="details-toggle">
          Details &amp; next steps <span aria-hidden="true">⌄</span>
        </summary>
        {!!career.subjects?.length && (
          <div className="trait-block">
            <h4 className="micro-label">Subjects to explore</h4>
            <div className="subject-pills">
              {career.subjects.map((subject) => (
                <span key={subject} className="subject-pill">
                  {subject}
                </span>
              ))}
            </div>
          </div>
        )}
        <div className="trait-block">
          <h4 className="micro-label">Try this</h4>
          <ul className="trait-bullets">
            {(career.activities ?? [career.guidance?.project].filter(Boolean)).map((activity) => (
              <li key={activity}>{activity}</li>
            ))}
          </ul>
        </div>
      </details>
    </li>
  );
}
