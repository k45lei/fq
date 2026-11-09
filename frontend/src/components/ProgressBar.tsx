interface Props {
  current: number;
  total: number;
  /** Shown above the bar, e.g. "Interests". Omitted when there's only one section. */
  section?: string;
}

export default function ProgressBar({ current, total, section }: Props) {
  const percent = total === 0 ? 0 : Math.round((current / total) * 100);

  return (
    <div>
      <div className="flex items-baseline justify-between gap-4 text-sm">
        {section ? (
          <span className="font-medium">{section}</span>
        ) : (
          <span className="text-muted">Your answers</span>
        )}
        <span className="tabular-nums text-muted">
          {Math.min(current + 1, total)} of {total}
        </span>
      </div>

      <div
        className="mt-2.5 h-1 w-full overflow-hidden rounded-full bg-line"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Progress through the questionnaire"
      >
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-500 ease-out"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
