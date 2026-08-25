/**
 * ProgressRing — the composite-score ring on option cards (§15) and the
 * brief-completeness meter (F2), plus a determinate/indeterminate bar for jobs.
 *
 * Two things it gets right that a naive ring gets wrong:
 *  - It is exposed as `role="progressbar"` with min/max/now and a text label,
 *    so a screen reader reads "Score 78 out of 100" rather than skipping an SVG.
 *  - Colour is never the only signal: the number sits in the middle. A red ring
 *    and a green ring are the same shape to a colour-blind reviewer.
 */

import { cn } from './cn';

export type ScoreBand = 'low' | 'mid' | 'high';

/**
 * Bands match the solver gate in §5.6: a composite below 55 is not presentable
 * at all, so 55 and 75 are the meaningful edges rather than round numbers.
 */
export function scoreBand(value: number): ScoreBand {
  if (value >= 75) return 'high';
  if (value >= 55) return 'mid';
  return 'low';
}

const BAND_STROKE: Record<ScoreBand, string> = {
  low: 'text-fail',
  mid: 'text-warn',
  high: 'text-pass',
};

export interface ProgressRingProps {
  /** 0–100. */
  value: number;
  /** Accessible name: "Composite score", "Brief completeness". */
  label: string;
  size?: number | undefined;
  strokeWidth?: number | undefined;
  /** Small caption under the number, e.g. "score" or "%". */
  caption?: string | undefined;
  /** Force a colour band instead of deriving it from the value. */
  band?: ScoreBand | undefined;
  className?: string | undefined;
}

export function ProgressRing({
  value,
  label,
  size = 56,
  strokeWidth = 5,
  caption,
  band,
  className,
}: ProgressRingProps): JSX.Element {
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  const r = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * r;
  const dash = (clamped / 100) * circumference;
  const stroke = BAND_STROKE[band ?? scoreBand(clamped)];

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      aria-label={label}
      aria-valuetext={`${clamped} out of 100`}
      className={cn('relative inline-flex shrink-0 items-center justify-center', className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-line"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circumference - dash}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          className={cn('transition-[stroke-dasharray] duration-300', stroke)}
        />
      </svg>
      <span className="absolute flex flex-col items-center leading-none">
        <span
          className="font-semibold text-ink garh-nums"
          style={{ fontSize: Math.round(size * 0.3) }}
        >
          {clamped}
        </span>
        {caption === undefined ? null : (
          <span className="mt-0.5 text-[0.5rem] uppercase tracking-wide text-ink-subtle">{caption}</span>
        )}
      </span>
    </div>
  );
}

export interface ProgressBarProps {
  /** 0–100, or `null` for indeterminate work whose end we honestly don't know. */
  value: number | null;
  label: string;
  /** Right-aligned status text: "3 of 5 checks", "queued behind 2 jobs". */
  detail?: string | undefined;
  className?: string | undefined;
}

/**
 * §15 forbids a fake progress bar. When the worker cannot report real progress,
 * pass `value={null}` and the bar renders as an indeterminate sweep — honest
 * about not knowing — instead of creeping to 90% and waiting.
 */
export function ProgressBar({ value, label, detail, className }: ProgressBarProps): JSX.Element {
  const indeterminate = value === null;
  const clamped = indeterminate ? 0 : Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-ink-muted">{label}</span>
        {detail === undefined ? null : (
          <span className="text-2xs text-ink-subtle garh-nums">{detail}</span>
        )}
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={indeterminate ? undefined : 0}
        aria-valuemax={indeterminate ? undefined : 100}
        aria-valuenow={indeterminate ? undefined : clamped}
        className="h-1.5 w-full overflow-hidden rounded-full bg-surface-muted"
      >
        {indeterminate ? (
          <div className="h-full w-1/5 animate-indeterminate rounded-full bg-brand" />
        ) : (
          <div
            className="h-full rounded-full bg-brand transition-[width] duration-300"
            style={{ width: `${clamped}%` }}
          />
        )}
      </div>
    </div>
  );
}
