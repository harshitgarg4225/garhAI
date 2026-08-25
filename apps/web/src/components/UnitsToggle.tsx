/**
 * UnitsToggle — ft-in ⇄ m, in the project top bar.
 *
 * This changes DISPLAY ONLY. The model is integer millimetres and does not
 * move; `projects.units` records the preference and every formatter reads it.
 * The label spells that out on hover, because "units" in CAD usually means
 * "the file's units" and here it does not.
 *
 * ft-in is the default per §15 ("Indian defaults: ft-in primary display"), and
 * the toggle keeps gaj out of it — gaj is a plot-area unit, shown alongside
 * sq ft by `formatPlotArea`, not a length system you switch into.
 */

import type { UnitsDisplay } from '@garh/model';
import { Tooltip, cn } from '@garh/ui';

export interface UnitsToggleProps {
  value: UnitsDisplay;
  onChange: (value: UnitsDisplay) => void;
  disabled?: boolean | undefined;
  className?: string | undefined;
}

const OPTIONS: ReadonlyArray<{ value: UnitsDisplay; label: string; hint: string }> = [
  { value: 'ft-in', label: "ft-in", hint: `Show lengths as 12'-6".` },
  { value: 'm', label: 'm', hint: 'Show lengths as 3.81 m.' },
];

export function UnitsToggle({ value, onChange, disabled, className }: UnitsToggleProps): JSX.Element {
  return (
    <Tooltip
      delayMs={400}
      content="Display units only — drawings are always dimensioned in millimetres."
    >
      <div
        role="radiogroup"
        aria-label="Display units"
        className={cn('inline-flex items-center rounded-md bg-surface-muted p-0.5', className)}
      >
        {OPTIONS.map((opt) => {
          const active = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={active}
              aria-label={opt.hint}
              disabled={disabled}
              onClick={() => onChange(opt.value)}
              className={cn(
                'garh-focus-ring h-7 rounded px-2.5 text-xs font-medium transition-colors',
                active ? 'bg-surface text-ink shadow-sm' : 'text-ink-muted hover:text-ink',
                disabled === true && 'cursor-not-allowed opacity-50',
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </Tooltip>
  );
}
