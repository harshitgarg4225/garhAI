/**
 * CanvasPlaceholder — the labelled hole where the Phase 4 editor goes.
 *
 * This is deliberately NOT a mock canvas. Golden rule 9 and §15's tone rules
 * cut both ways: a fake plan drawn in SVG would look like the product works,
 * and the first person to click it would lose trust in everything else on the
 * screen. So the area says what it is, which phase brings it, and what you can
 * do in the meantime.
 *
 * It still occupies the real geometry (fills the middle cell of the §12 grid,
 * shows the grid paper, carries the storey label), so the shell's layout is
 * exercised now rather than being rebuilt when the canvas lands.
 */

import type { ReactNode } from 'react';
import { Button, Icon, cn } from '@garh/ui';
import type { IconName } from '@garh/ui';

export interface CanvasPlaceholderProps {
  /** "Plan — Ground floor", "3D view". */
  title: string;
  /** The build phase that delivers the real thing: "Phase 4". */
  phase: string;
  /** One sentence on what will be here. */
  delivers: string;
  icon?: IconName | undefined;
  /** What the architect can usefully do right now instead. */
  action?: { label: string; onClick: () => void; icon?: IconName | undefined } | undefined;
  secondaryAction?: { label: string; onClick: () => void } | undefined;
  children?: ReactNode;
  className?: string | undefined;
}

export function CanvasPlaceholder({
  title,
  phase,
  delivers,
  icon = 'wall',
  action,
  secondaryAction,
  children,
  className,
}: CanvasPlaceholderProps): JSX.Element {
  return (
    <section
      aria-label={`${title} — not built yet`}
      className={cn(
        'relative flex h-full min-h-80 w-full items-center justify-center overflow-hidden bg-surface-sunken',
        className,
      )}
    >
      {/* Drafting grid. Purely decorative, hidden from the a11y tree. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            'linear-gradient(to right, rgb(var(--garh-line)) 1px, transparent 1px),' +
            'linear-gradient(to bottom, rgb(var(--garh-line)) 1px, transparent 1px)',
          backgroundSize: '24px 24px',
        }}
      />

      <div className="relative z-10 flex max-w-md flex-col items-center gap-3 rounded-xl border border-dashed border-line-strong bg-surface/90 px-8 py-9 text-center shadow-sm">
        <span
          className="flex h-11 w-11 items-center justify-center rounded-full bg-surface-muted text-ink-subtle"
          aria-hidden="true"
        >
          <Icon name={icon} size={20} />
        </span>
        <div>
          <h2 className="text-base font-semibold text-ink">{title}</h2>
          <p className="mt-1 text-sm leading-6 text-ink-muted">{delivers}</p>
          <p className="mt-2 text-xs font-medium uppercase tracking-wider text-ink-subtle">
            Arrives in {phase}
          </p>
        </div>
        {action === undefined && secondaryAction === undefined ? null : (
          <div className="flex flex-wrap items-center justify-center gap-2">
            {action === undefined ? null : (
              <Button variant="primary" size="sm" iconLeft={action.icon} onClick={action.onClick}>
                {action.label}
              </Button>
            )}
            {secondaryAction === undefined ? null : (
              <Button variant="ghost" size="sm" onClick={secondaryAction.onClick}>
                {secondaryAction.label}
              </Button>
            )}
          </div>
        )}
        {children}
      </div>
    </section>
  );
}
