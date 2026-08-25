/**
 * ComplianceChipStrip.tsx — the bottom strip (§12 "bottom compliance chip strip").
 *
 * Golden rule 5, literally: this never blocks anything. It has no modal, no
 * disabled state it can put another control into, and no path that prevents an
 * export. An architect can ignore every chip on it and still ship a drawing —
 * overrides are logged (§13), not prevented.
 *
 * DOM, not WebGL, on purpose. There are at most a few dozen chips, they are
 * text a screen reader must read and a user must be able to select and copy,
 * and §15 requires full keyboard operability of the panels. Canvas text is none
 * of those things. The §14 rule that bans DOM nodes applies to per-LABEL text
 * on the drawing (hundreds of nodes, redrawn per frame) — a chip strip is
 * chrome, and chrome belongs in the DOM.
 *
 * Each chip carries the four things §15 demands: severity colour, one line of
 * human text written by the rules layer, the citation on hover, and a "Fix it"
 * action when the pack returned a computable fix. Clicking the text selects and
 * zooms to the offending element.
 */

import { Button, Chip, ComplianceChip, cn } from '@garh/ui';

import type { ComplianceChipVM } from './mapping';

export interface ComplianceChipStripProps {
  /** `null` = nothing has been checked yet. Not the same as an empty array. */
  chips: readonly ComplianceChipVM[] | null;
  counts: { fail: number; warn: number; pass: number };
  checking: boolean;
  /** Select the offending elements and zoom the camera to them. */
  onFocus: (chip: ComplianceChipVM) => void;
  /** Apply the pack's auto-fix. Only offered when `fixAvailable`. */
  onFix?: ((chip: ComplianceChipVM) => void) | undefined;
  /** Open the full Compliance tab. */
  onOpenAll?: (() => void) | undefined;
  /** No plot or no rule pack yet — the strip explains rather than sits blank. */
  emptyHint?: string | undefined;
  className?: string | undefined;
}

export function ComplianceChipStrip({
  chips,
  counts,
  checking,
  onFocus,
  onFix,
  onOpenAll,
  emptyHint = 'Draw a plot and pick a city to start checking.',
  className,
}: ComplianceChipStripProps): JSX.Element {
  const hasChips = chips !== null && chips.length > 0;

  return (
    <div
      className={cn(
        'pointer-events-auto flex items-center gap-2 overflow-x-auto border-t border-line',
        'bg-surface/95 px-3 py-2 backdrop-blur',
        className,
      )}
      role="region"
      aria-label="Compliance"
      // Announced, but politely: a chip appearing must never steal focus from
      // the wall someone is drawing.
      aria-live="polite"
      aria-busy={checking}
    >
      <Summary counts={counts} checking={checking} evaluated={chips !== null} />

      <div className="flex min-w-0 flex-1 items-center gap-2">
        {chips === null ? (
          <span className="truncate text-xs text-ink-muted">{emptyHint}</span>
        ) : chips.length === 0 ? (
          <span className="truncate text-xs text-ink-muted">
            Nothing to fix on this plan right now.
          </span>
        ) : (
          chips.map((chip) => (
            <ComplianceChip
              key={chip.key}
              status={chip.status}
              message={chip.message}
              cite={chip.cite}
              ruleId={chip.ruleId}
              confidence={chip.confidence}
              // Only offered when the ENGINE said a fix is computable. A "Fix
              // it" button that opens a dialog asking what to do is worse than
              // no button.
              onFix={chip.fixAvailable && onFix !== undefined ? () => onFix(chip) : undefined}
              // A chip with nothing to point at is still readable — it just
              // cannot zoom. `focus === null` is how we know (a plot-wide FAR
              // rule names no element).
              onSelect={chip.focus === null ? undefined : () => onFocus(chip)}
              size="sm"
            />
          ))
        )}
      </div>

      {onOpenAll === undefined ? null : (
        <Button variant="ghost" size="sm" onClick={onOpenAll}>
          {hasChips ? 'See all checks' : 'Compliance'}
        </Button>
      )}
    </div>
  );
}

function Summary({
  counts,
  checking,
  evaluated,
}: {
  counts: { fail: number; warn: number; pass: number };
  checking: boolean;
  evaluated: boolean;
}): JSX.Element | null {
  if (!evaluated) return null;
  if (checking && counts.fail === 0 && counts.warn === 0) {
    return (
      <Chip severity="neutral" size="sm" icon="info">
        Checking…
      </Chip>
    );
  }
  if (counts.fail === 0 && counts.warn === 0) {
    return (
      <Chip severity="pass" size="sm">
        All checks pass
      </Chip>
    );
  }
  return (
    <Chip severity={counts.fail > 0 ? 'fail' : 'warn'} size="sm">
      {counts.fail > 0 ? `${String(counts.fail)} to fix` : `${String(counts.warn)} to check`}
    </Chip>
  );
}
