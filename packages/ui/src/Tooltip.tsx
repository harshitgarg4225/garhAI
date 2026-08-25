/**
 * Tooltip — hover AND focus, Escape to dismiss, `aria-describedby` wired.
 *
 * Used heavily by compliance chips ("cite on hover", §15) and by the tool rail
 * (icon + keyboard shortcut). Two rules it follows that most tooltips break:
 *
 *  1. It opens on keyboard focus, not just pointer hover, so the citation is
 *     reachable without a mouse.
 *  2. It is `aria-describedby`, never `aria-label` — the trigger keeps its own
 *     name and the tooltip adds detail. A tooltip that REPLACES the name means
 *     the content is load-bearing and belongs in the page, not a hover.
 *
 * Positioning is CSS-only (absolutely positioned relative to a wrapper). No
 * floating-ui: our tooltips are small, near the pointer, and the panels they
 * live in scroll rather than clip. `viewportSafe` flips a tooltip that would
 * run off the right edge, which covers the one real failure case.
 */

import { cloneElement, useId, useRef, useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { cn } from './cn';
import { useOnEscape } from './hooks';

export type TooltipPlacement = 'top' | 'bottom' | 'left' | 'right';

/**
 * Combine the trigger's own `aria-describedby` with the tooltip's id.
 *
 * ARIA allows a space-separated id list and reads them in order, so appending
 * keeps a field's hint and error announced first and the tooltip last. When the
 * bubble is closed the id must go away entirely — pointing `aria-describedby`
 * at an element that is not in the DOM makes some screen readers announce
 * nothing at all, including the description the trigger already had.
 *
 * Exported for tests; not re-exported from the package index.
 */
export function mergeDescribedBy(
  existing: string | undefined,
  tooltipId: string,
  open: boolean,
): string | undefined {
  const own = existing === undefined || existing.trim() === '' ? undefined : existing.trim();
  if (!open) return own;
  return own === undefined ? tooltipId : `${own} ${tooltipId}`;
}

const PLACEMENT: Record<TooltipPlacement, string> = {
  top: 'bottom-full left-1/2 mb-1.5 -translate-x-1/2',
  bottom: 'top-full left-1/2 mt-1.5 -translate-x-1/2',
  left: 'right-full top-1/2 mr-1.5 -translate-y-1/2',
  right: 'left-full top-1/2 ml-1.5 -translate-y-1/2',
};

export interface TooltipProps {
  /** The hover content. Keep it to a sentence; anything longer is a Popover. */
  content: ReactNode;
  placement?: TooltipPlacement | undefined;
  /** ms before showing on hover. 0 for toolbars, ~400 for dense tables. */
  delayMs?: number | undefined;
  /** Max width class for the bubble. */
  widthClass?: string | undefined;
  className?: string | undefined;
  children: ReactElement;
}

export function Tooltip({
  content,
  placement = 'top',
  delayMs = 250,
  widthClass = 'max-w-xs',
  className,
  children,
}: TooltipProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const id = useId();

  useOnEscape(open, () => setOpen(false));

  const show = (): void => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(true), delayMs);
  };
  const hide = (): void => {
    if (timer.current !== null) clearTimeout(timer.current);
    setOpen(false);
  };

  /**
   * `aria-describedby` goes on the TRIGGER ITSELF, not on a wrapper.
   *
   * A wrapper `<span>` is not focusable and has no accessible role, so a
   * description hung on it is never announced — the citation on a compliance
   * chip would be visible to a mouse user and silent to a screen-reader user,
   * which is exactly the failure §15 accessibility is trying to prevent. Cloning
   * the child puts the attribute on the element that actually receives focus.
   *
   * Any `aria-describedby` the child already carries is preserved (space
   * separated, per the ARIA spec) rather than overwritten — a form control
   * inside a tooltip keeps its own hint and error ids.
   *
   * Note for component authors: a trigger that is a custom component must
   * forward `aria-describedby` to its rendered element. `Button`, `IconButton`,
   * `Input` and `Select` do so by spreading; `Chip` and `Badge` accept it
   * explicitly for this reason.
   */
  const childDescribedBy = (children.props as { 'aria-describedby'?: string })['aria-describedby'];
  const describedBy = mergeDescribedBy(childDescribedBy, id, open);

  const trigger = cloneElement(children, { 'aria-describedby': describedBy } as Partial<
    Record<string, unknown>
  >);

  return (
    <span
      className={cn('relative inline-flex', className)}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocusCapture={() => setOpen(true)}
      onBlurCapture={hide}
    >
      {trigger}
      {open ? (
        <span
          role="tooltip"
          id={id}
          className={cn(
            'pointer-events-none absolute z-popover w-max rounded-md border border-line bg-surface',
            'px-2.5 py-1.5 text-xs leading-4 text-ink shadow-md animate-fade-in',
            widthClass,
            PLACEMENT[placement],
          )}
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Convenience for tool buttons: "Wall  W". The shortcut is rendered as a <kbd>
 * so it reads as a key, not as part of the sentence.
 */
export function ShortcutHint({ label, keys }: { label: string; keys: string }): JSX.Element {
  return (
    <span className="flex items-center gap-2 whitespace-nowrap">
      <span>{label}</span>
      <kbd className="rounded border border-line bg-surface-muted px-1 py-px font-mono text-2xs text-ink-muted">
        {keys}
      </kbd>
    </span>
  );
}
