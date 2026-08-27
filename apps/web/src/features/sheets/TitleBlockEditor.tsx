/**
 * TitleBlockEditor.tsx — the §7 title-block editor: firm fields, logo, sheet
 * numbering, the auto revision table, and the dimToJamb house style.
 *
 * Saved on the **firm**, not the project (`firms.settings.drawings`), because these
 * are drafting-office conventions: a practice has one letterhead and one dimensioning
 * habit, and re-typing them per project is how a set ends up with the wrong client
 * name on sheet 4. The project's own name is filled in by the server at generation
 * time when the template leaves it blank.
 *
 * Two fields deserve their explanation in the UI, and get it:
 *
 *   - **dimToJamb** is §7 step 6. Openings are dimensioned to their centreline by
 *     default; some offices dimension to the jamb. It changes every opening dimension
 *     on every floor plan, so it is labelled with what it does, not with its name.
 *   - **Sheet numbering** is a prefix only. The number itself (`A-01`… `A-06`, with a
 *     letter suffix per storey or elevation) is generated, because a hand-typed number
 *     that disagrees with the set's order is a drawing returned at the counter.
 */

import { useCallback, useEffect, useState } from 'react';

import { Badge, Button, Icon, Input, Field, cn, useToast } from '@garh/ui';

import { AppError } from '../../lib/errors';
import {
  fetchDrawingPreferences,
  saveDrawingPreferences,
  type DrawingPreferences,
  type RevisionRow,
  type TitleBlock,
} from './api';

const MAX_REVISIONS = 12;

export interface TitleBlockEditorProps {
  onSaved?: (preferences: DrawingPreferences) => void;
  className?: string;
}

export function TitleBlockEditor({ onSaved, className }: TitleBlockEditorProps): JSX.Element {
  const { toast } = useToast();
  const [prefs, setPrefs] = useState<DrawingPreferences | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchDrawingPreferences()
      .then((next) => {
        if (!controller.signal.aborted) setPrefs(next);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof AppError
            ? `${cause.message} ${cause.action}`
            : 'Could not load your title block.',
        );
      });
    return () => controller.abort();
  }, []);

  const patchBlock = useCallback((patch: Partial<TitleBlock>) => {
    setPrefs((current) =>
      current ? { ...current, titleBlock: { ...current.titleBlock, ...patch } } : current,
    );
  }, []);

  const save = useCallback(async () => {
    if (!prefs) return;
    setSaving(true);
    try {
      const saved = await saveDrawingPreferences({
        titleBlock: prefs.titleBlock,
        dimToJamb: prefs.dimToJamb,
        sheetNumberPrefix: prefs.sheetNumberPrefix,
        defaultScaleDenominator: prefs.defaultScaleDenominator,
        defaultSheetSize: prefs.defaultSheetSize,
        revisions: prefs.revisions,
      });
      setPrefs(saved);
      onSaved?.(saved);
      toast({
        severity: 'pass',
        title: 'Title block saved',
        description: 'Every set you generate from now on uses it.',
      });
    } catch (cause: unknown) {
      toast({
        severity: 'fail',
        title: "Couldn't save your title block",
        description: cause instanceof AppError ? cause.message : 'Something went wrong.',
        action: { label: 'Try again', onClick: () => void save() },
      });
    } finally {
      setSaving(false);
    }
  }, [onSaved, prefs, toast]);

  if (error) {
    return (
      <p
        className={cn(
          'rounded-md border border-line bg-surface p-4 text-xs text-fail-ink',
          className,
        )}
      >
        {error}
      </p>
    );
  }
  if (!prefs) {
    return (
      <p
        className={cn(
          'rounded-md border border-line bg-surface p-4 text-xs text-ink-muted',
          className,
        )}
      >
        Loading your title block…
      </p>
    );
  }

  const block = prefs.titleBlock;
  const setRevision = (index: number, patch: Partial<RevisionRow>) =>
    setPrefs((current) =>
      current
        ? {
            ...current,
            revisions: current.revisions.map((row, i) =>
              i === index ? { ...row, ...patch } : row,
            ),
          }
        : current,
    );

  return (
    <section
      className={cn('overflow-hidden rounded-md border border-line bg-surface', className)}
      aria-label="Title block"
      data-testid="title-block-editor"
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Title block</h2>
        <Badge tone={prefs.source === 'firm' ? 'outline' : 'neutral'}>
          {prefs.source === 'firm' ? 'Your firm’s template' : 'Defaults — not saved yet'}
        </Badge>
        <span className="ml-auto">
          <Button
            size="sm"
            onClick={() => void save()}
            disabled={saving}
            data-testid="title-block-save"
          >
            {saving ? 'Saving…' : 'Save for the firm'}
          </Button>
        </span>
      </div>

      <div className="grid gap-3 px-4 py-4 sm:grid-cols-2">
        <Field label="Firm name">
          {(props) => (
            <Input
              {...props}
              value={block.firmName}
              onChange={(e) => patchBlock({ firmName: e.currentTarget.value })}
            />
          )}
        </Field>
        <Field label="Logo URL" hint="Printed in the title block. Must be an https link.">
          {(props) => (
            <Input
              {...props}
              value={block.logoUrl ?? ''}
              placeholder="https://…"
              onChange={(e) => patchBlock({ logoUrl: e.currentTarget.value || null })}
            />
          )}
        </Field>
        <Field label="Project title" hint="Left blank, the project's own name is used.">
          {(props) => (
            <Input
              {...props}
              value={block.projectName}
              onChange={(e) => patchBlock({ projectName: e.currentTarget.value })}
            />
          )}
        </Field>
        <Field label="Client">
          {(props) => (
            <Input
              {...props}
              value={block.clientName}
              onChange={(e) => patchBlock({ clientName: e.currentTarget.value })}
            />
          )}
        </Field>
        <Field label="Drawn by">
          {(props) => (
            <Input
              {...props}
              value={block.drawnBy}
              onChange={(e) => patchBlock({ drawnBy: e.currentTarget.value })}
            />
          )}
        </Field>
        <Field label="Checked by">
          {(props) => (
            <Input
              {...props}
              value={block.checkedBy}
              onChange={(e) => patchBlock({ checkedBy: e.currentTarget.value })}
            />
          )}
        </Field>
        <Field label="Date" hint="DD-MM-YYYY, as it prints.">
          {(props) => (
            <Input
              {...props}
              value={block.date}
              placeholder="01-04-2026"
              onChange={(e) => patchBlock({ date: e.currentTarget.value })}
            />
          )}
        </Field>
        <Field label="Sheet number prefix" hint="A-01, A-02… Some corporations want AR.">
          {(props) => (
            <Input
              {...props}
              value={prefs.sheetNumberPrefix}
              maxLength={4}
              onChange={(e) =>
                setPrefs((c) => (c ? { ...c, sheetNumberPrefix: e.currentTarget.value } : c))
              }
            />
          )}
        </Field>
        <div className="sm:col-span-2">
          <Field label="Notes" hint="Printed under the title block on every sheet.">
            {(props) => (
              <Input
                {...props}
                value={block.notes}
                onChange={(e) => patchBlock({ notes: e.currentTarget.value })}
              />
            )}
          </Field>
        </div>
      </div>

      <label className="flex cursor-pointer items-start gap-2 border-t border-line px-4 py-3">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={prefs.dimToJamb}
          data-testid="dim-to-jamb"
          onChange={(e) => setPrefs((c) => (c ? { ...c, dimToJamb: e.currentTarget.checked } : c))}
        />
        <span>
          <span className="text-xs font-medium text-ink">
            Dimension openings to the jamb, not the centreline
          </span>
          <span className="mt-0.5 block text-2xs leading-4 text-ink-muted">
            Off (the default) prints one dimension to each door and window centre. On prints the
            clear opening between jambs. It changes every opening dimension on every floor plan, so
            pick your office's habit once.
          </span>
        </span>
      </label>

      {/* Revision table (§7 "auto revision table") */}
      <div className="border-t border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold text-ink">Revisions</h3>
          <span className="text-2xs text-ink-muted">Printed on every sheet, newest last.</span>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            disabled={prefs.revisions.length >= MAX_REVISIONS}
            onClick={() =>
              setPrefs((c) =>
                c
                  ? {
                      ...c,
                      revisions: [
                        ...c.revisions,
                        { revision: nextRevisionLetter(c.revisions), date: '', note: '' },
                      ],
                    }
                  : c,
              )
            }
            data-testid="revision-add"
          >
            <Icon name="plus" size={13} /> Add row
          </Button>
        </div>
        {prefs.revisions.length === 0 ? (
          <p className="mt-2 text-2xs text-ink-muted">
            No revisions yet. The first submission is normally revision A with no row.
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {prefs.revisions.map((row, index) => (
              <li
                key={index}
                className="flex flex-wrap items-center gap-2"
                data-testid="revision-row"
              >
                <Input
                  aria-label={`Revision ${index + 1} letter`}
                  className="w-16"
                  value={row.revision}
                  maxLength={8}
                  onChange={(e) => setRevision(index, { revision: e.currentTarget.value })}
                />
                <Input
                  aria-label={`Revision ${index + 1} date`}
                  className="w-32"
                  placeholder="DD-MM-YYYY"
                  value={row.date}
                  onChange={(e) => setRevision(index, { date: e.currentTarget.value })}
                />
                <Input
                  aria-label={`Revision ${index + 1} note`}
                  className="min-w-48 flex-1"
                  placeholder="What changed"
                  value={row.note}
                  onChange={(e) => setRevision(index, { note: e.currentTarget.value })}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove revision ${index + 1}`}
                  onClick={() =>
                    setPrefs((c) =>
                      c ? { ...c, revisions: c.revisions.filter((_, i) => i !== index) } : c,
                    )
                  }
                >
                  <Icon name="trash" size={13} />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/** A → B → C. Falls back to a number past Z rather than wrapping to A silently. */
export function nextRevisionLetter(rows: readonly RevisionRow[]): string {
  if (rows.length === 0) return 'A';
  const last = rows[rows.length - 1]?.revision ?? 'A';
  const code = last.trim().toUpperCase().charCodeAt(0);
  if (last.trim().length === 1 && code >= 65 && code < 90) {
    return String.fromCharCode(code + 1);
  }
  return `R${rows.length + 1}`;
}

export default TitleBlockEditor;
