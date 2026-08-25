/**
 * Dialog — modal, focus-trapped, Escape-closable, portalled to <body>.
 *
 * Portalled because a dialog rendered inside the project shell would be clipped
 * by the inspector's `overflow-hidden` and would inherit the canvas stacking
 * context. Focus is trapped and restored by `useFocusTrap`; the backdrop is
 * inert to screen readers; the title is wired to `aria-labelledby` so the
 * dialog announces itself.
 *
 * Not built on <dialog>: `showModal()` top-layer rendering is well supported
 * now, but it fights the Tailwind backdrop and its close-on-Escape cannot be
 * intercepted for "you have unsaved changes" flows.
 */

import { useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { ReactNode } from 'react';
import { cn } from './cn';
import { useBodyScrollLock, useFocusTrap, useOnEscape } from './hooks';
import { Button, IconButton } from './Button';

export type DialogSize = 'sm' | 'md' | 'lg' | 'xl';

const SIZES: Record<DialogSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
};

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** One warm sentence under the title. Optional but usually right. */
  description?: ReactNode | undefined;
  size?: DialogSize | undefined;
  /** Footer actions, right-aligned. Put the confirming action last. */
  footer?: ReactNode | undefined;
  /** Set false for destructive/irreversible flows where a stray click hurts. */
  dismissOnBackdrop?: boolean | undefined;
  /** Hide the × — only for flows the user must resolve. */
  hideClose?: boolean | undefined;
  className?: string | undefined;
  children?: ReactNode;
}

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  size = 'md',
  footer,
  dismissOnBackdrop = true,
  hideClose = false,
  className,
  children,
}: DialogProps): JSX.Element | null {
  const panelRef = useRef<HTMLDivElement>(null);
  const base = useId();
  const titleId = `${base}-title`;
  const descId = `${base}-desc`;

  useFocusTrap(panelRef, open);
  useOnEscape(open, () => onOpenChange(false));
  useBodyScrollLock(open);

  if (!open) return null;
  if (typeof document === 'undefined') return null;

  return createPortal(
    <div className="fixed inset-0 z-dialog flex items-end justify-center p-0 sm:items-center sm:p-6">
      <div
        className="absolute inset-0 animate-fade-in bg-scrim/55 backdrop-blur-[1px]"
        aria-hidden="true"
        onClick={dismissOnBackdrop ? () => onOpenChange(false) : undefined}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description === undefined ? undefined : descId}
        className={cn(
          'relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-xl border border-line',
          'bg-surface shadow-lg animate-pop-in sm:rounded-xl',
          SIZES[size],
          className,
        )}
      >
        <div className="flex items-start gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-semibold leading-6 text-ink">
              {title}
            </h2>
            {description !== undefined ? (
              <p id={descId} className="mt-1 text-sm leading-5 text-ink-muted">
                {description}
              </p>
            ) : null}
          </div>
          {hideClose ? null : (
            <IconButton
              label="Close"
              icon="x"
              size="sm"
              onClick={() => onOpenChange(false)}
              className="-mr-1 -mt-1"
            />
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer === undefined ? null : (
          <div className="flex items-center justify-end gap-2 border-t border-line bg-surface-muted px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  cancelLabel?: string | undefined;
  destructive?: boolean | undefined;
  busy?: boolean | undefined;
  onConfirm: () => void;
}

/**
 * The one confirmation shape. §15 says everything is undoable, so a confirm
 * dialog should be rare — prefer doing the thing and offering "Undo" in a
 * toast. Reserve this for actions the op log genuinely cannot reverse
 * (revoking a share link, deleting a project).
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Keep it',
  destructive = false,
  busy = false,
  onConfirm,
}: ConfirmDialogProps): JSX.Element {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      size="sm"
      dismissOnBackdrop={!busy}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? 'danger' : 'primary'}
            onClick={onConfirm}
            loading={busy}
            loadingLabel="Working on it"
          >
            {confirmLabel}
          </Button>
        </>
      }
    />
  );
}
