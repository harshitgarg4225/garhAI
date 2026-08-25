/**
 * VastuWheel — the §15 "Vastu compass wheel": eight 45° sectors (N at the top,
 * compass convention) plus the brahmasthan centre, each painted by the WORST
 * status of the vastu rules that live there, with the per-rule breakdown
 * listed alongside. Data comes from the option's compliance rows — the engine
 * computed every status; this component only colours what it was told (§5.4).
 */

import { Tooltip, cn } from '@garh/ui';

import { COMPASS_SECTORS, sectorLabelPoint, sectorPath } from './planGeometry';
import type { VastuWheel as VastuWheelData, WheelStatus } from './stats';

export interface VastuWheelProps {
  readonly wheel: VastuWheelData;
  /** Diameter in px. Cards use the default; CompareTwo passes a larger one. */
  readonly size?: number | undefined;
  /** Show the per-rule list next to the wheel (cards keep it in the expander). */
  readonly showRules?: boolean | undefined;
  readonly className?: string | undefined;
}

const FILL: Readonly<Record<WheelStatus, string>> = {
  pass: 'fill-pass-soft',
  warn: 'fill-warn-soft',
  fail: 'fill-fail-soft',
  none: 'fill-surface-muted',
};

const STROKE: Readonly<Record<WheelStatus, string>> = {
  pass: 'stroke-pass-line',
  warn: 'stroke-warn-line',
  fail: 'stroke-fail-line',
  none: 'stroke-line',
};

const DOT: Readonly<Record<Exclude<WheelStatus, 'none'>, string>> = {
  pass: 'bg-pass',
  warn: 'bg-warn',
  fail: 'bg-fail',
};

export function VastuWheel({
  wheel,
  size = 96,
  showRules = false,
  className,
}: VastuWheelProps): JSX.Element | null {
  if (!wheel.applicable) return null;

  const c = 50; // internal 100×100 coordinate space; `size` only scales it
  const rOuter = 46;
  const rInner = 18;
  const rLabel = 32;

  const described = wheel.rules
    .map((r) => `${r.title}: ${r.status}`)
    .join('; ');

  return (
    <div className={cn('flex items-start gap-3', className)}>
      <svg
        viewBox="0 0 100 100"
        width={size}
        height={size}
        role="img"
        aria-label={`Vastu ${wheel.score} out of 100. ${described}`}
        className="shrink-0"
      >
        {COMPASS_SECTORS.map((sector) => {
          const status = wheel.sectors[sector];
          const at = sectorLabelPoint(c, c, rLabel, sector);
          return (
            <g key={sector}>
              <path
                d={sectorPath(c, c, rInner, rOuter, sector)}
                strokeWidth={1}
                className={cn(FILL[status], STROKE[status])}
              />
              <text
                x={at.x}
                y={at.y}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={9}
                className="fill-current text-ink-subtle"
              >
                {sector}
              </text>
            </g>
          );
        })}
        {/* Brahmasthan — the centre cell Vastu keeps open. */}
        <circle
          cx={c}
          cy={c}
          r={rInner - 3}
          strokeWidth={1}
          className={cn(FILL[wheel.center], STROKE[wheel.center])}
        />
        <text
          x={c}
          y={c}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize={11}
          fontWeight={600}
          className="fill-current text-ink"
        >
          {wheel.score}
        </text>
      </svg>

      {showRules ? (
        <ul className="min-w-0 space-y-1 text-xs text-ink-muted">
          {[...wheel.rules, ...wheel.unplaced].map((rule) => (
            <li key={rule.ruleId} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className={cn('h-1.5 w-1.5 shrink-0 rounded-full', DOT[rule.status])}
              />
              {rule.message !== null ? (
                <Tooltip content={rule.message}>
                  <span className="truncate">{rule.title}</span>
                </Tooltip>
              ) : (
                <span className="truncate">{rule.title}</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
