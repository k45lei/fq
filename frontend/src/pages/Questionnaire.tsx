import { useEffect, useRef } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { useLatestReport, useQuestionnaire, useSubmitSection } from "../api/queries";
import Layout from "../components/Layout";
import ProgressBar from "../components/ProgressBar";
import QuestionCard from "../components/QuestionCard";
import { isAnswered, isSectionComplete, toAnswerInput } from "../lib/answers";
import { SECTION_META } from "../lib/sections";
import { useQuestionnaireStore } from "../store/questionnaire";

export default function Questionnaire() {
  const navigate = useNavigate();
  const advanceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (advanceTimer.current) {
        clearTimeout(advanceTimer.current);
      }
    },
    [],
  );
  const [searchParams] = useSearchParams();
  const {
    responseId: storedResponseId,
    reportId,
    answers,
    currentQuestionId,
    setAnswer,
    goTo,
    complete,
    begin,
  } = useQuestionnaireStore();
  // The response may come from the store (this browser started it) or from the URL — the
  // "continue" link on a report, which a signed-in visitor on another device reaches
  // without ever having started it here.
  const urlResponseId = searchParams.get("response_id");
  const responseId = urlResponseId ?? storedResponseId;

  useEffect(() => {
    if (responseId && responseId !== storedResponseId) {
      begin(responseId);
    }
  }, [responseId, storedResponseId, begin]);
  // Asked about this response, not in general: the entitlement gate looks entitlements up by
  // user *and* response, so a preview with no response to ask about can't widen. Without
  // this, a visitor who has just paid would still be served only the free sections.
  const { data: questionnaire, isPending, error } = useQuestionnaire(responseId ?? undefined);
  const submit = useSubmitSection();

  // Resume at the first unfinished stage, including after a payment return.
  const completed = new Set(questionnaire?.completed_sections ?? []);
  const pendingSections = (questionnaire?.sections ?? []).filter(
    (section) => !completed.has(section),
  );
  const questions = (questionnaire?.questions ?? []).filter(
    (question) => question.category === pendingSections[0],
  );
  // Positioned by question id rather than by index: the question list is filtered — a
  // completed section drops out of it — so an index would be a position in the wrong list
  // and would resume the visitor mid-section. The id survives the filtering.
  //
  // `findIndex` returns -1 when the stored id isn't in the list — a question from a
  // questionnaire version that has since changed, say — and the first question is the
  // right thing to fall back to.
  const index = Math.max(
    0,
    questions.findIndex((item) => item.id === currentQuestionId),
  );
  const question = questions[index];
  const canAdvance = question ? isAnswered(question, answers[question.id]) : false;
  const isLast = index === questions.length - 1;

  // The neighbour, in the filtered list. `undefined` at either end, which the callers
  // already handle by disabling the button.
  const next = questions[index + 1];
  const previous = questions[index - 1];

  // Number keys pick an option or scale point; Enter moves to the next question.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!question) {
        return;
      }

      const target = event.target as HTMLElement | null;

      if (target?.tagName === "TEXTAREA" || target?.tagName === "INPUT") {
        return;
      }

      if (event.key === "Enter" && canAdvance && !isLast) {
        if (next) {
          goTo(next.id);
        }

        return;
      }

      const digit = Number(event.key);

      if (!Number.isInteger(digit) || digit < 1) {
        return;
      }

      if (question.answer_type === "scale") {
        const min = question.scale_min ?? 1;
        const max = question.scale_max ?? 5;

        const value = min + digit - 1;

        if (value <= max) {
          setAnswer(question.id, { numeric: value });
        }

        return;
      }

      const option = question.options[digit - 1];

      if (!option) {
        return;
      }

      if (question.answer_type === "single_choice") {
        setAnswer(question.id, { optionIds: [option.id] });
      } else if (question.answer_type === "multi_choice") {
        const selected = answers[question.id]?.optionIds ?? [];
        const limit = question.max_selections;
        if (selected.includes(option.id)) {
          setAnswer(question.id, { optionIds: selected.filter((id) => id !== option.id) });
        } else if (limit === null || selected.length < limit) {
          setAnswer(question.id, { optionIds: [...selected, option.id] });
        }
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [question, questions, next, answers, canAdvance, isLast, index, goTo, setAnswer]);

  // Remember where the visitor is, so closing the tab and coming back lands on the same
  // question. Recorded by id, because the question list is filtered as sections complete.
  useEffect(() => {
    if (question && question.id !== currentQuestionId) {
      goTo(question.id);
    }
  }, [question, currentQuestionId, goTo]);

  if (!responseId) {
    return <Navigate to="/" replace />;
  }

  if (isPending || responseId !== storedResponseId) {
    return <Centered>Loading…</Centered>;
  }

  if (error || !questionnaire) {
    return (
      <Centered>
        {error instanceof ApiError ? error.message : "Couldn't load the questionnaire."}
      </Centered>
    );
  }

  // Already finished: there's nothing to answer, and resubmitting would be rejected anyway.
  //
  // *Unless* the visitor has paid since — in which case the paid sections are still to do,
  // and the report they have is the free one. The check is on the sections that remain, not
  // on whether a report id happens to be stored, because a stored report id is what a
  // visitor who has just paid still has.
  //
  // The stored report only counts if it belongs to *this* response. A visitor who came
  // here from another response's report has a stored report that says nothing about this
  // one, and treating it as finished would send them to the wrong report.
  //
  // This comes before the "no questions" check, because a fully-answered response has no
  // questions left to show — and the right answer to that is the report, not a dead end.
  const ownReportId = storedResponseId === responseId ? reportId : null;
  if (ownReportId && pendingSections.length === 0) {
    return <Navigate to={`/report/${ownReportId}`} replace />;
  }

  if (!question) {
    // Everything is answered. The report is found by id, and the id lives in the store —
    // so this is only reachable if it was lost, which is rare but shouldn't be a dead end.
    // The newest report for the response is findable without an id, so use it.
    if (ownReportId) {
      return <Navigate to={`/report/${ownReportId}`} replace />;
    }

    return <LatestReportRedirect responseId={responseId} />;
  }

  const everythingAnswered = isSectionComplete(questions, answers);
  const meta = SECTION_META[question.category];

  // Progress is relative to the current stage.
  const inSection = questions.filter((item) => item.category === question.category);
  const positionInSection = inSection.findIndex((item) => item.id === question.id);

  const finish = async () => {
    try {
      const result = await submit.mutateAsync({
        responseId,
        section: question.category,
        answers: questions.map((item) => toAnswerInput(item, answers[item.id] ?? {})),
      });
      complete(result.report_id);
      navigate(`/report/${result.report_id}`);
    } catch {
      // Keep the draft intact; the mutation error offers a safe retry below.
    }
  };

  const subsectionNames: Record<string, string> = {
    interests: "Interests",
    abilities: "Abilities",
    workStyle: "Work style",
    values: "Values",
    "self-achievement": "Self & achievement",
    motivators: "Motivators",
    "structure-feedback": "Structure & feedback",
    "work-environment": "Work environment",
    self: "Knowing yourself",
    others: "Reading others",
    world: "In the real world",
  };
  const subsections = [...new Set(questions.map((item) => item.subsection).filter(Boolean))];

  return (
    <Layout>
      <div className="mb-5 flex items-center justify-between">
        <p className="text-lg font-bold">{meta.label}</p>
        <Link to="/" aria-label="Exit quiz" className="rounded-full p-2 text-muted">
          ✕
        </Link>
      </div>
      <ProgressBar current={positionInSection} total={inSection.length} section={meta.label} />

      <div className="mt-5 flex flex-wrap gap-3 text-xs">
        {subsections.map((section) => (
          <span
            key={section}
            className={section === question.subsection ? "font-bold text-accent" : "text-muted"}
          >
            {subsectionNames[section!] ?? section}
          </span>
        ))}
      </div>
      <section
        key={question.id}
        data-question-id={question.id}
        className="animate-rise mt-9 rounded-2xl border border-line bg-surface p-6 shadow-card sm:p-8"
      >
        <QuestionCard
          question={question}
          draft={answers[question.id]}
          onChange={(answer) => {
            setAnswer(question.id, answer);
            if (advanceTimer.current) {
              clearTimeout(advanceTimer.current);
            }
            if (
              next &&
              question.answer_type !== "multi_choice" &&
              question.answer_type !== "free_text"
            ) {
              advanceTimer.current = setTimeout(() => goTo(next.id), 250);
            }
          }}
        />
      </section>

      {submit.error && (
        <p className="mt-4 text-sm text-red-700">
          {submit.error instanceof ApiError
            ? submit.error.message
            : "Couldn't submit your answers."}
        </p>
      )}

      <div className="mt-7 flex items-center justify-between">
        <button
          type="button"
          onClick={() => {
            if (advanceTimer.current) {
              clearTimeout(advanceTimer.current);
            }
            if (previous) {
              goTo(previous.id);
            }
          }}
          disabled={index === 0}
          className="rounded-xl px-4 py-3 text-muted transition hover:text-ink disabled:invisible"
        >
          Back
        </button>

        {isLast ? (
          <button
            type="button"
            onClick={finish}
            disabled={!everythingAnswered || submit.isPending}
            className="rounded-xl bg-accent px-7 py-3 font-medium text-white transition hover:bg-accent-strong disabled:opacity-40"
          >
            {submit.isPending ? "Building your results…" : "See my results"}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => next && goTo(next.id)}
            disabled={!canAdvance}
            className="rounded-xl bg-accent px-7 py-3 font-medium text-white transition hover:bg-accent-strong disabled:opacity-40"
          >
            Next
          </button>
        )}
      </div>

      <p className="mt-10 text-center text-sm text-muted">
        Drafts stay in this browser. Completed stages are saved to your account or guest session.
      </p>
    </Layout>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <Layout>
      <p className="py-16 text-center text-muted">{children}</p>
    </Layout>
  );
}

/**
 * Find the newest report for a response and go to it.
 *
 * Only reached when the response is fully answered and the report id isn't in the store —
 * which shouldn't happen, but a dead end is worse than one extra request. The newest report
 * is the right one: reports are append-only, so the last is the most complete.
 */
function LatestReportRedirect({ responseId }: { responseId: string }) {
  const { data, isPending, error } = useLatestReport(responseId);

  if (isPending) {
    return <Centered>Finding your results…</Centered>;
  }

  if (error || !data) {
    return (
      <Centered>
        We couldn&rsquo;t find your results.{" "}
        <Link to="/" className="text-accent underline underline-offset-4">
          Start again
        </Link>
      </Centered>
    );
  }
  return <Navigate to={`/report/${data.id}`} replace />;
}
