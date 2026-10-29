import { useState } from "react";
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

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [school, setSchool] = useState("");

  const questionCount = questionnaire?.questions.length ?? 0;
  const minutes = questionCount > 0 ? Math.max(2, Math.round((questionCount * 15) / 60)) : null;

  const signedIn = Boolean(me?.signed_in);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    // Signed in, these come from the account rather than the form. The server ignores them
    // in favour of the session anyway — sending the real values keeps the two consistent.
    const result = await start.mutateAsync({
      name: signedIn ? (me?.name ?? "") : name.trim(),
      email: signedIn ? (me?.email ?? "") : email.trim(),
      school: school.trim() || null,
    });
    begin(result.response_id);
    navigate("/questionnaire");
  };

  return (
    <Layout>
      <Link to="/" className="text-sm text-muted underline underline-offset-4 hover:text-ink">
        Back
      </Link>

      <h1 className="mt-6 font-display text-4xl leading-tight font-semibold">
        Before we start
      </h1>
      <p className="mt-3 text-muted text-pretty">
        {signedIn ? "One optional detail" : "Three details"}, then straight into the questions.
        {minutes ? ` It takes about ${minutes} minutes.` : ""}
      </p>

      <form
        onSubmit={submit}
        className="mt-8 rounded-2xl border border-line bg-surface p-6 shadow-card sm:p-7"
      >
        {/* Signed in, we already know who this is. Offering an email field here was the bug:
            typing a different address attached the response to another account, where it was
            invisible in their history and unreachable once the cookie expired. */}
        {signedIn ? (
          <div className="rounded-xl border border-line bg-warm px-5 py-4">
            <p className="text-sm text-muted">Continuing as</p>
            <p className="mt-0.5 font-medium">{me?.name ?? me?.email}</p>
            {me?.name && me?.email && <p className="text-sm text-muted">{me.email}</p>}
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Your name"
              value={name}
              onChange={setName}
              autoComplete="name"
              required
            />
            <Field
              label="Email"
              type="email"
              value={email}
              onChange={setEmail}
              autoComplete="email"
              required
            />
          </div>
        )}

        <div className="mt-4">
          <Field label="School or college" value={school} onChange={setSchool} hint="Optional." />
        </div>

        <p className="mt-4 text-sm text-muted">
          {signedIn
            ? "Your results are saved to your account."
            : "We'll send a copy of your results to that email."}
        </p>

        {start.error && (
          <p className="mt-4 text-sm text-red-700">
            {start.error instanceof ApiError
              ? start.error.message
              : "Something went wrong. Please try again."}
          </p>
        )}

        <button
          type="submit"
          disabled={start.isPending}
          className="mt-6 w-full rounded-xl bg-accent px-5 py-3.5 font-medium text-white transition hover:bg-accent-strong disabled:opacity-50 sm:w-auto sm:px-10"
        >
          {start.isPending ? "Starting…" : "Start the questionnaire"}
        </button>
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
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <input
        {...rest}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1.5 w-full rounded-xl border border-line bg-surface px-4 py-2.5 outline-none transition focus:border-accent"
      />
      {hint && <span className="mt-1 block text-sm text-muted">{hint}</span>}
    </label>
  );
}
