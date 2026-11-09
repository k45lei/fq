import { useEffect } from "react";
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
  const [searchParams] = useSearchParams();
  const { responseId: storedResponseId, reportId, answers, currentQuestionId, setAnswer, goTo, complete } =
    useQuestionnaireStore();
  // The response may come from the store (this browser started it) or from the URL — the
  // "continue" link on a report, which a signed-in visitor on another device reaches
  // without ever having started it here.
  const urlResponseId = searchParams.get("response_id");
  const responseId = urlResponseId ?? storedResponseId;
  // Asked about this response, not in general: the entitlement gate looks entitlements up by
  // user *and* response, so a preview with no response to ask about can't widen. Without
  // this, a visitor who has just paid would still be served only the free sections.
  const { data: questionnaire, isPending, error } = useQuestionnaire(responseId ?? undefined);
  const submit = useSubmitSection();

  // A section can only be submitted once, so a section that's already done is not shown
  // again. This is what makes the paid-sections-after-payment flow work: the visitor has
  // finished interests, buys the report, comes back, and is shown the two sections they
  // haven't answered — rather than the interests questions they already submitted, which
  // the server would refuse.
  const completed = new Set(questionnaire?.completed_sections ?? []);
  const pendingSections = (questionnaire?.sections ?? []).filter(
    (section) => !completed.has(section),
  );
  const questions = (questionnaire?.questions ?? []).filter(
    (question) => !completed.has(question.category),
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

  // Number keys pick an option or a scale point; Enter moves on. Forty questions is a lot of
  // mousing, and the shortcut hints on each option are only honest if they work.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!question) return;
      const target = event.target as HTMLElement | null;
      if (target?.tagName === "TEXTAREA" || target?.tagName === "INPUT") return;

      if (event.key === "Enter" && canAdvance && !isLast) {
        if (next) goTo(next.id);
        return;
      }

      const digit = Number(event.key);
      if (!Number.isInteger(digit) || digit < 1) return;

      if (question.answer_type === "scale") {
        const min = question.scale_min ?? 1;
        const max = question.scale_max ?? 5;
        if (digit >= min && digit <= max) setAnswer(question.id, { numeric: digit });
        return;
      }

      const option = question.options[digit - 1];
      if (!option) return;

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
    if (question && question.id !== currentQuestionId) goTo(question.id);
  }, [question, currentQuestionId, goTo]);

  if (!responseId) return <Navigate to="/" replace />;

  if (isPending) return <Centered>Loading…</Centered>;
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
    if (ownReportId) return <Navigate to={`/report/${ownReportId}`} replace />;
    return <LatestReportRedirect responseId={responseId} />;
  }

  const everythingAnswered = isSectionComplete(questions, answers);
  const meta = SECTION_META[question.category];

  // Position within the section, so progress reads as "3 of 14" rather than "17 of 40" —
  // the same length feels finite when it's broken into named stretches.
  const inSection = questions.filter((item) => item.category === question.category);
  const positionInSection = inSection.findIndex((item) => item.id === question.id);

  const finish = async () => {
    // One request per section, in order. Each produces a report — a response being scored more
    // than once as more of it is answered is the design, not a quirk — and the last one, built
    // from every answer, is the one worth showing.
    //
    // Only the sections still to do are submitted: a section can only be submitted once, and
    // the server refuses a second attempt. That's what lets a visitor who has already
    // finished the free tier come back and answer only the paid sections.
    let newReportId: string | null = null;

    for (const section of pendingSections) {
      const sectionQuestions = questions.filter((item) => item.category === section);
      if (sectionQuestions.length === 0) continue;

      const result = await submit.mutateAsync({
        responseId,
        section,
        answers: sectionQuestions.map((item) => toAnswerInput(item, answers[item.id] ?? {})),
      });
      newReportId = result.report_id;
    }

    if (!newReportId) return;
    complete(newReportId);
    navigate(`/report/${newReportId}`);
  };

  return (
    <Layout>
      <ProgressBar
        current={positionInSection}
        total={inSection.length}
        section={pendingSections.length > 1 ? meta.label : undefined}
      />

      <section
        key={question.id}
        className="animate-rise mt-9 rounded-2xl border border-line bg-surface p-6 shadow-card sm:p-8"
      >
        <QuestionCard
          question={question}
          draft={answers[question.id]}
          onChange={(answer) => setAnswer(question.id, answer)}
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
          onClick={() => previous && goTo(previous.id)}
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
        Saved as you go — close this and come back whenever.
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

  if (isPending) return <Centered>Finding your results…</Centered>;
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
