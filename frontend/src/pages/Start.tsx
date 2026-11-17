import { useId, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useMe, useQuestionnaire, useStartResponse } from "../api/queries";
import Layout from "../components/Layout";
import { useQuestionnaireStore } from "../store/questionnaire";

/**
 * Just the form. Its own page so the landing can stay a single screen — a capture form at the
 * bottom of a scroll is both easy to miss and the thing most likely to be abandoned.
 */
export default function Start() {
  const navigate = useNavigate();
  const start = useStartResponse();
  const { data: questionnaire } = useQuestionnaire();
  const { data: me } = useMe();
  const { begin } = useQuestionnaireStore();

  const [firstName, setFirstName] = useState("");
  const [email, setEmail] = useState("");
  const [lastName, setLastName] = useState("");
  const [school, setSchool] = useState("");

  const questionCount = questionnaire?.questions.length ?? 0;
  const minutes = questionCount > 0 ? Math.max(2, Math.round((questionCount * 15) / 60)) : null;

  const signedIn = Boolean(me?.signed_in);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    // Signed in, these come from the account rather than the form. The server ignores them
    // in favour of the session anyway — sending the real values keeps the two consistent.
    try {
      const result = await start.mutateAsync({
        name: signedIn
          ? (me?.name ?? "")
          : [firstName.trim(), lastName.trim()].filter(Boolean).join(" "),
        email: signedIn ? (me?.email ?? "") : email.trim(),
        school: school.trim() || null,
      });
      begin(result.response_id);
      navigate("/questionnaire");
    } catch {
      // The mutation error is shown in the form.
    }
  };

  return (
    <Layout variant="gatherinfo">
      <div className="step-header">
        <div className="eyebrow-pill">
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <span>
            {minutes ? `Takes about ${minutes} minutes` : "Discover your interests"} · Free
          </span>
        </div>
        <h1>
          One quick step
          <br />
          before we start.
        </h1>
        <p className="step-subtitle">
          Tell us who you are so we can personalize your career report and save your results to
          review later.
        </p>
      </div>
      <form onSubmit={submit} className="form-card">
        {signedIn ? (
          <div className="signed-in-summary">
            <p>Continuing as</p>
            <strong>{me?.name ?? me?.email}</strong>
            {me?.name && me?.email && <p>{me.email}</p>}
          </div>
        ) : (
          <>
            <div className="form-row">
              <Field
                label="First name"
                value={firstName}
                onChange={setFirstName}
                autoComplete="given-name"
                maxLength={100}
                required
              />
              <Field
                label="Last name"
                value={lastName}
                onChange={setLastName}
                autoComplete="family-name"
                maxLength={99}
              />
            </div>
            <Field
              label="Email"
              type="email"
              value={email}
              onChange={setEmail}
              autoComplete="email"
              maxLength={320}
              hint="Your report is saved with your guest session or account."
              required
            />
          </>
        )}
        <Field
          label="School"
          value={school}
          onChange={setSchool}
          autoComplete="organization"
          maxLength={300}
          placeholder="e.g. Central Toronto Academy"
        />
        {start.error && (
          <p className="form-error" role="alert">
            {start.error instanceof ApiError
              ? start.error.message
              : "Something went wrong. Please try again."}
          </p>
        )}
        <div className="form-actions">
          <button type="submit" disabled={start.isPending} className="btn-primary">
            {start.isPending ? (
              "Starting…"
            ) : (
              <>
                Start my quiz <span>→</span>
              </>
            )}
          </button>
          <Link to="/before-quiz" className="btn-back">
            Back
          </Link>
        </div>
        <div className="privacy-footer">
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            <polyline points="9 12 11 14 15 10" />
          </svg>
          <span>
            Your completed answers and report are saved privately. Sign in to access them on another
            device.
          </span>
        </div>
      </form>
    </Layout>
  );
}

function Field({
  label,
  value,
  onChange,
  hint,
  type = "text",
  ...rest
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  type?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange" | "value" | "type">) {
  const id = useId();

  return (
    <div className="form-group">
      <label htmlFor={id}>
        {label}{" "}
        {rest.required ? (
          <span className="required-star" aria-hidden="true">
            *
          </span>
        ) : (
          <span className="optional-text">(optional)</span>
        )}
      </label>
      <input
        {...rest}
        id={id}
        aria-label={label}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && <span className="helper-text">{hint}</span>}
    </div>
  );
}
