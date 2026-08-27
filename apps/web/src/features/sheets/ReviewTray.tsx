/**
 * ReviewTray.tsx — where notes go when a re-solve moves the ground under them
 * (§7 "Annotation anchoring", decision D13).
 *
 * The honest scope, stated in the UI and not only in a spec:
 *
 *   - a note follows its element by **id** across manual and copilot edits — nothing
 *     to do, and nothing appears here;
 *   - when a layout no longer has that id, the note lands in this list;
 *   - **there is no fuzzy re-matching.** The engine will not guess a nearby wall. The
 *     architect re-attaches it to an element they pick, or deletes it.
 *
 * The policy sentence is not written in this file — it is `tray.policy`, straight from
 * the server, so the promise the UI makes and the rule the engine enforces cannot
 * drift apart in a copy edit.
 *
 * Both actions are **ops** (op 32, `annotation.set`), dispatched through the model
 * store exactly like a wall move. That is what makes "re-attached the wrong element"
 * a Cmd-Z away, and what puts the change in the version timeline.
 */

import { useCallback, useMemo, useState } from 'react';

import { Badge, Button, EmptyState, Icon, Select, Spinner, cn, useToast } from '@garh/ui';

import { useModelStore } from '../../stores/model';
import {
  annotationText,
  deleteAnnotationOp,
  elementLabel,
  reattachAnnotationOp,
  type ReviewTray as ReviewTrayData,
  type SheetAnnotation,
} from './api';

export interface ReviewTrayProps {
  tray: ReviewTrayData | null;
  loading: boolean;
  /** Re-read the tray after an op lands, so counts stay true. */
  onRefresh: () => void;
  className?: string;
}

export function ReviewTray({ tray, loading, onRefresh, className }: ReviewTrayProps): JSX.Element {
  const { toast } = useToast();
  const dispatch = useModelStore((state) => state.dispatch);
  const [busy, setBusy] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});

  const orphans = tray?.orphaned ?? [];
  const total = (tray?.attachedCount ?? 0) + orphans.length;

  const run = useCallback(
    (annotation: SheetAnnotation, kind: 'reattach' | 'delete') => {
      const modelId = annotation.modelAnnotationId;
      if (!modelId) {
        // The projection has no op-log id for this row, so no op can address it. Say
        // so rather than firing an op with an id the fold will reject.
        toast({
          severity: 'fail',
          title: "This note can't be edited yet",
          description: 'It has no id in the design history.',
          action: { label: 'Regenerate the set', onClick: onRefresh },
        });
        return;
      }
      const target = picked[annotation.id];
      if (kind === 'reattach' && !target) return;
      setBusy(annotation.id);
      try {
        const op =
          kind === 'delete'
            ? deleteAnnotationOp(modelId)
            : reattachAnnotationOp(modelId, target as string);
        const result = dispatch([op], { source: 'manual' });
        if (!result.ok) {
          toast({
            severity: 'fail',
            title: kind === 'delete' ? "Couldn't delete that note" : "Couldn't re-attach that note",
            description:
              result.issues[0]?.message ?? 'The change was rejected, so nothing was applied.',
            action: { label: 'Try again', onClick: onRefresh },
          });
          return;
        }
        toast({
          severity: 'pass',
          title: kind === 'delete' ? 'Note deleted' : 'Note re-attached',
          description:
            kind === 'delete'
              ? 'Undo brings it back.'
              : `Now anchored to ${elementLabel(target as string)}.`,
        });
        onRefresh();
      } finally {
        setBusy(null);
      }
    },
    [dispatch, onRefresh, picked, toast],
  );

  const header = useMemo(
    () => (
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Review tray</h2>
        {orphans.length > 0 ? (
          <Badge tone="warn" data-testid="review-tray-count">
            {orphans.length} need{orphans.length === 1 ? 's' : ''} attention
          </Badge>
        ) : (
          <Badge tone="outline">All notes attached</Badge>
        )}
        <span className="ml-auto flex items-center gap-2 text-2xs text-ink-muted">
          {total > 0 ? `${tray?.attachedCount ?? 0} of ${total} anchored` : null}
          {loading ? <Spinner size={12} /> : null}
          <Button size="sm" variant="ghost" onClick={onRefresh} aria-label="Re-check anchors">
            <Icon name="refresh" size={13} />
          </Button>
        </span>
      </div>
    ),
    [loading, onRefresh, orphans.length, total, tray?.attachedCount],
  );

  return (
    <section
      className={cn('overflow-hidden rounded-md border border-line bg-surface', className)}
      aria-label="Review tray"
      data-testid="review-tray"
    >
      {header}

      {/* The server's own words. Deliberately not a local string. */}
      {tray ? (
        <p className="flex items-start gap-2 border-b border-line bg-surface-muted px-4 py-2.5 text-2xs leading-4 text-ink-muted">
          <Icon name="info" size={13} className="mt-px shrink-0" />
          <span data-testid="review-tray-policy">{tray.policy}</span>
        </p>
      ) : null}

      {orphans.length === 0 ? (
        <div className="px-4 py-6">
          <EmptyState
            size="sm"
            icon="check-circle"
            title="Nothing to review"
            description={
              tray?.reconciled
                ? 'Every note is still attached to the element it was placed on.'
                : 'No notes are flagged. Re-check to compare them against the current design.'
            }
            demoAction={{
              notApplicable: 'The review tray is per project; the demo offer is on the dashboard.',
            }}
          />
        </div>
      ) : (
        <ul className="divide-y divide-line">
          {orphans.map((annotation) => (
            <li key={annotation.id} className="px-4 py-3" data-testid="review-tray-item">
              <div className="flex flex-wrap items-start gap-2">
                <Icon name="alert-triangle" size={14} className="mt-0.5 shrink-0 text-warn-ink" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-ink">
                    {annotationText(annotation) || (
                      <em className="text-ink-muted">Untitled note</em>
                    )}
                  </p>
                  <p className="mt-0.5 text-2xs text-ink-muted">
                    {annotation.sheetNumber ? `Sheet ${annotation.sheetNumber} · ` : ''}
                    was attached to{' '}
                    <span className="font-mono">
                      {annotation.anchorElementId
                        ? elementLabel(annotation.anchorElementId)
                        : 'nothing'}
                    </span>
                    , which this layout does not have.
                  </p>
                </div>
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-2 pl-6">
                <Select
                  aria-label={`Re-attach note to an element on ${annotation.sheetNumber ?? 'this sheet'}`}
                  value={picked[annotation.id] ?? ''}
                  onValueChange={(value) =>
                    setPicked((current) => ({ ...current, [annotation.id]: value }))
                  }
                  placeholder="Pick an element on this sheet…"
                  options={annotation.reattachCandidates.map((id) => ({
                    value: id,
                    label: elementLabel(id),
                  }))}
                  className="min-w-56"
                />
                <Button
                  size="sm"
                  disabled={!picked[annotation.id] || busy === annotation.id}
                  onClick={() => void run(annotation, 'reattach')}
                  data-testid="review-tray-reattach"
                >
                  Re-attach
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy === annotation.id}
                  onClick={() => void run(annotation, 'delete')}
                  data-testid="review-tray-delete"
                >
                  <Icon name="trash" size={13} /> Delete
                </Button>
                {annotation.reattachCandidates.length === 0 ? (
                  <span className="text-2xs text-ink-muted">
                    That sheet has no elements to attach to — regenerate the set first.
                  </span>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default ReviewTray;
