import { scaleValues } from "../lib/answers";
import type { Question } from "../api/types";
import type { DraftAnswer } from "../store/questionnaire";

interface Props {
  question: Question;
  draft: DraftAnswer | undefined;
  onChange: (answer: DraftAnswer) => void;
}

export default function QuestionCard({ question, draft, onChange }: Props) {
  const selected = draft?.optionIds ?? [];
  const multiple = question.answer_type === "multi_choice";
  const limit = question.max_selections;
  const atLimit = multiple && limit !== null && selected.length >= limit;
  const agreement = question.scale_min === 1 && question.scale_max === 5;
  const labels = ["Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"];
  const hints = ["No way", "Not really", "Maybe", "Yeah", "Totally"];

  const toggleOption = (id: string) => {
    if (!multiple) {
      onChange({ optionIds: [id] });
      return;
    }

    if (selected.includes(id)) {
      onChange({ optionIds: selected.filter((option) => option !== id) });
    } else if (!atLimit) {
      onChange({ optionIds: [...selected, id] });
    }
  };

  const scaleLabel = (value: number, index: number) => {
    if (agreement) {
      return labels[index];
    }
    if (value === 0) {
      return "Balanced";
    }
    if (value === question.scale_min) {
      return question.scale_min_label ?? String(value);
    }
    if (value === question.scale_max) {
      return question.scale_max_label ?? String(value);
    }
    return `Leans ${value < 0 ? "left" : "right"}`;
  };

  return (
    <div>
      <h1 className="question-text" id={`question-${question.id}`}>
        {question.prompt}
      </h1>
      {question.help_text && <p className="question-help">{question.help_text}</p>}
      {question.answer_type === "scale" ? (
        <div className="options-list" role="radiogroup" aria-labelledby={`question-${question.id}`}>
          {!agreement && (
            <p className="question-help">
              {question.scale_min_label} ↔ {question.scale_max_label}
            </p>
          )}
          {scaleValues(question).map((value, index) => (
            <label key={value} className="option-row">
              <span className="option-left">
                <input
                  type="radio"
                  name={question.id}
                  value={value}
                  checked={draft?.numeric === value}
                  onChange={() => onChange({ numeric: value })}
                />
                <span className="radio-circle" aria-hidden="true" />
                <span className="option-main-label">{scaleLabel(value, index)}</span>
              </span>
              <span className="option-sub-label">{agreement ? hints[index] : value}</span>
            </label>
          ))}
        </div>
      ) : question.answer_type === "free_text" ? (
        <textarea
          aria-labelledby={`question-${question.id}`}
          value={draft?.text ?? ""}
          onChange={(event) => onChange({ text: event.target.value })}
          rows={6}
          maxLength={10000}
          className="question-textarea"
          placeholder="A few sentences is plenty."
        />
      ) : (
        <div
          className="options-list"
          role={multiple ? "group" : "radiogroup"}
          aria-labelledby={`question-${question.id}`}
        >
          {question.options.map((option) => (
            <label key={option.id} className="option-row">
              <span className="option-left">
                <input
                  type={multiple ? "checkbox" : "radio"}
                  name={question.id}
                  value={option.id}
                  checked={selected.includes(option.id)}
                  disabled={atLimit && !selected.includes(option.id)}
                  onChange={() => toggleOption(option.id)}
                />
                <span
                  className={multiple ? "checkbox-square" : "radio-circle"}
                  aria-hidden="true"
                />
                <span className="option-main-label">{option.label}</span>
              </span>
            </label>
          ))}
          {multiple && limit !== null && (
            <p className="question-help" aria-live="polite">
              {selected.length} of {limit} selected
              {atLimit ? " — deselect one to change your mind" : ""}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
