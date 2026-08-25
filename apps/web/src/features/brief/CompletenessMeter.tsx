/**
 * CompletenessMeter — how much of the brief is captured, and what to answer
 * next (§F2; golden rule 8 — the empty state teaches).
 *
 * Renders the live score from {@link computeCompleteness} — the same pure
 * function that stamps `brief.update.completeness` — so the ring on this
 * panel, the number in the op log and the dashboard chip can never disagree.
 *
 * The missing list is ranked by weight: the top entries are literally "the
 * missing answers that would most improve the generated options" from the F2
 * spec. Each is a button when the host passes `onJumpTo`, so the meter is a
 * table of contents for the form, not a scold.
 */

import { ProgressRing, SkeletonRegion, SkeletonText, cn } from '@garh/ui';

import { useBrief } from './useBrief';

export interface CompletenessMeterProps {
  /**
   * Jump to the form section that answers a missing item. Ids are the stable
   * `CompletenessItem.id`s ('bedrooms', 'budget', …). Omit to render the list
   * as plain text.
   */
  readonly onJumpTo?: ((itemId: string) => void) | undefined;
  /** How many missing items to list. Default 4 — enough to teach, not a wall. */
  readonly maxItems?: number | undefined;
  readonly className?: string | undefined;
}

export function CompletenessMeter({
  onJumpTo,
  maxItems = 4,
  className,
}: CompletenessMeterProps): JSX.Element {
  const { completeness, ready } = useBrief();

  if (!ready) {
    return (
      <SkeletonRegion label="Loading brief completeness" className={cn('flex gap-4', className)}>
        <span className="h-14 w-14 shrink-0 animate-pulse rounded-full bg-surface-muted" />
        <SkeletonText lines={3} className="flex-1" />
      </SkeletonRegion>
    );
  }

  const { score, missing } = completeness;
  const top = missing.slice(0, maxItems);
  const rest = missing.length - top.length;

  return (
    <div className={cn('flex items-start gap-4', className)}>
      <ProgressRing value={score} label="Brief completeness" caption="brief" />

      <div className="min-w-0 flex-1">
        {score === 0 ? (
          <>
            <h3 className="text-sm font-semibold text-ink">Nothing captured yet</h3>
            <p className="mt-0.5 text-xs leading-5 text-ink-muted">
              Start with bedrooms and a budget — those two shape the plans most. Or paste the
              client&rsquo;s message below and let us read it into the form.
            </p>
          </>
        ) : missing.length === 0 ? (
          <>
            <h3 className="text-sm font-semibold text-ink">Brief complete</h3>
            <p className="mt-0.5 text-xs leading-5 text-ink-muted">
              Everything the plan generator asks for is answered. You can still refine any field —
              every answer stays editable.
            </p>
          </>
        ) : (
          <>
            <h3 className="text-sm font-semibold text-ink">What would help most next</h3>
            <ul className="mt-1 space-y-1">
              {top.map((item) => (
                <li key={item.id} className="flex items-baseline gap-1.5 text-xs leading-5">
                  <span aria-hidden="true" className="text-ink-subtle">
                    ·
                  </span>
                  {onJumpTo === undefined ? (
                    <span className="text-ink">{item.label}</span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onJumpTo(item.id)}
                      className="garh-focus-ring rounded-sm text-left text-ink underline-offset-2 hover:underline"
                    >
                      {item.label}
                    </button>
                  )}
                  <span className="truncate text-ink-subtle">— {item.hint}</span>
                </li>
              ))}
            </ul>
            {rest > 0 ? (
              <p className="mt-1 text-2xs text-ink-subtle">
                +{rest} smaller question{rest === 1 ? '' : 's'} after these.
              </p>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

export default CompletenessMeter;
