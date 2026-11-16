/**
 * Renders one question, whichever kind it is.
 *
 * Presentational: it takes a draft answer and reports changes upward. It knows nothing about
 * the response, the API or persistence.
 */

import { scaleValues } from "../lib/answers";
import type { Question } from "../api/types";
import type { DraftAnswer } from "../store/questionnaire";

interface Props {
  question: Question;
  draft: DraftAnswer | undefined;
  onChange: (answer: DraftAnswer) => void;
}

const CHOICE_BASE =
  "group flex w-full items-center gap-3.5 rounded-xl border px-4 py-3.5 text-left " +
  "transition duration-150 disabled:cursor-not-allowed disabled:opacity-35";

const CHOICE_IDLE = "border-line bg-surface hover:border-accent/50 hover:shadow-card";
const CHOICE_ON = "border-accent bg-accent-soft shadow-card";

export default function QuestionCard({ question, draft, onChange }: Props) {
  const selected = draft?.optionIds ?? [];

  return (
    <div>
      <h2 className="text-balance text-2xl leading-snug font-medium tracking-tight">
        {question.prompt}
      </h2>
      {question.help_text && <p className="mt-2.5 text-muted">{question.help_text}</p>}

      <div className="mt-7">
        {question.answer_type === "single_choice" && (
          <div className="space-y-2.5">
            {question.options.map((option, index) => {
              const isSelected = selected.includes(option.id);
              return (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => onChange({ optionIds: [option.id] })}
                  className={`${CHOICE_BASE} ${isSelected ? CHOICE_ON : CHOICE_IDLE}`}
                >
                  <Marker selected={isSelected} shape="circle" />
                  <span className="flex-1">{option.label}</span>
                  <Hint index={index} />
                </button>
              );
            })}
          </div>
        )}

        {question.answer_type === "multi_choice" && (
          <MultiChoice question={question} selected={selected} onChange={onChange} />
        )}

        {question.answer_type === "scale" && (
          <Scale question={question} value={draft?.numeric} onChange={onChange} />
        )}

        {question.answer_type === "free_text" && (
          <textarea
            value={draft?.text ?? ""}
            onChange={(event) => onChange({ text: event.target.value })}
            rows={6}
            placeholder="A few sentences is plenty."
            className="w-full resize-none rounded-xl border border-line bg-surface px-4 py-3.5 leading-relaxed outline-none transition focus:border-accent focus:shadow-card"
          />
        )}
      </div>
    </div>
  );
}

function Marker({ selected, shape }: { selected: boolean; shape: "circle" | "square" }) {
  return (
    <span
      className={`grid size-5 shrink-0 place-items-center border-2 transition ${
        shape === "circle" ? "rounded-full" : "rounded-md"
      } ${selected ? "border-accent bg-accent" : "border-line group-hover:border-accent/50"}`}
    >
      {selected && (
        <svg viewBox="0 0 12 12" className="size-3 text-white" aria-hidden>
          <path
            d="M2.5 6.2 4.8 8.5 9.5 3.8"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </span>
  );
}

/** The keyboard shortcut for an option. Hidden on touch, where there's no keyboard to hint at. */
function Hint({ index }: { index: number }) {
  if (index > 8) {
    return null;
  }

  return (
    <kbd className="hidden shrink-0 rounded border border-line px-1.5 py-0.5 font-sans text-xs text-muted sm:block">
      {index + 1}
    </kbd>
  );
}

function MultiChoice({
  question,
  selected,
  onChange,
}: {
  question: Question;
  selected: string[];
  onChange: (answer: DraftAnswer) => void;
}) {
  const limit = question.max_selections;
  const atLimit = limit !== null && selected.length >= limit;

  const toggle = (optionId: string) => {
    const next = selected.includes(optionId)
      ? selected.filter((id) => id !== optionId)
      : [...selected, optionId];
    onChange({ optionIds: next });
  };

  return (
    <div className="space-y-2.5">
      {question.options.map((option, index) => {
        const isSelected = selected.includes(option.id);
        return (
          <button
            key={option.id}
            type="button"
            aria-pressed={isSelected}
            // Blocking selection at the limit is friendlier than letting someone pick a fourth
            // and rejecting the whole submission later.
            disabled={atLimit && !isSelected}
            onClick={() => toggle(option.id)}
            className={`${CHOICE_BASE} ${isSelected ? CHOICE_ON : CHOICE_IDLE}`}
          >
            <Marker selected={isSelected} shape="square" />
            <span className="flex-1">{option.label}</span>
            <Hint index={index} />
          </button>
        );
      })}

      {limit !== null && (
        <p className="pt-1 text-sm text-muted">
          {selected.length} of {limit} selected
          {atLimit && " — deselect one to change your mind"}
        </p>
      )}
    </div>
  );
}

function Scale({
  question,
  value,
  onChange,
}: {
  question: Question;
  value: number | undefined;
  onChange: (answer: DraftAnswer) => void;
}) {
  const values = scaleValues(question);

  const agreement = question.scale_min === 1 && question.scale_max === 5;
  const labels = ["Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"];
  const hints = ["No way", "Not really", "Maybe", "Yeah", "Totally"];

  const labelFor = (option: number, index: number) => {
    if (agreement) {
      return labels[index];
    }

    if (option === 0) {
      return "Balanced";
    }

    if (option === question.scale_min) {
      return question.scale_min_label;
    }

    if (option === question.scale_max) {
      return question.scale_max_label;
    }

    return `Leans ${option < 0 ? "left" : "right"}`;
  };

  return (
    <div className="space-y-3">
      {!agreement && (
        <p className="text-sm text-muted">
          {question.scale_min_label} ↔ {question.scale_max_label}
        </p>
      )}
      {values.map((option, index) => (
        <button
          key={option}
          type="button"
          aria-pressed={value === option}
          onClick={() => onChange({ numeric: option })}
          className={`${CHOICE_BASE} ${value === option ? CHOICE_ON : CHOICE_IDLE}`}
        >
          <Marker selected={value === option} shape="circle" />
          <span className="flex-1">{labelFor(option, index)}</span>
          <span className="text-sm text-muted">{agreement ? hints[index] : option}</span>
        </button>
      ))}
    </div>
  );
}
