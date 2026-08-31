/**
 * ReferenceBoard.tsx — the project's inspiration board (§11).
 *
 * A client sends a kitchen they love, a facade from a magazine, a hotel
 * bathroom. Before this, the product could take exactly one picture and only
 * its filename, which was stored and never read. There was nothing to say which
 * part of the house a picture was for, what to take from it, or what to leave.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE ARCHITECT ANNOTATES; THE PRODUCT ONLY ASKS ABOUT CONFLICTS
 * ════════════════════════════════════════════════════════════════════════════
 * Every card asks the same four questions, and the architect answers all four.
 * Nothing here guesses at them and nothing reads the image: a UI that filled in
 * "you probably meant the cabinets" would be wrong exactly often enough to be
 * untrustworthy, and its mistakes would be invisible in a render.
 *
 * What the product contributes is the review below the board — the questions it
 * can actually justify, all deterministic: two pictures both set to match
 * closely for the same view, a picture that cannot inform the view being
 * rendered, and a picture nobody has annotated. Each states what happens if the
 * architect does nothing, because a question with an unknown default is one
 * people dismiss.
 *
 * The review also shows the exact prompt fragments the model will receive. That
 * is deliberate and it is the feature's honesty: the instruction the architect
 * wrote and the instruction the model gets are visibly the same text, so
 * "did it actually use my reference?" is answerable before the render, not
 * argued about after it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Badge, Button, Icon, Select, Spinner, Textarea, cn } from '@garh/ui';

import {
  REFERENCE_INTENTS,
  REFERENCE_SCOPES,
  type ProjectReference,
  type ReferenceIntent,
  type ReferenceScope,
} from '../../lib/api';
import { selectBoard, selectUnannotatedCount, useReferenceStore } from './store';

/** Human wording for the vocabulary. The values themselves are the API's. */
const SCOPE_LABEL: Readonly<Record<ReferenceScope, string>> = {
  'whole-house': 'The whole house',
  facade: 'Facade / elevation',
  interior: 'Interiors generally',
  kitchen: 'Kitchen',
  living: 'Living / dining',
  bedroom: 'Bedroom',
  bathroom: 'Bathroom',
  landscape: 'Landscape / setback',
  material: 'A material or finish',
};

const INTENT_LABEL: Readonly<Record<ReferenceIntent, string>> = {
  match: 'Match closely',
  guide: 'Take the feel',
  avoid: 'Avoid this',
};

/**
 * `avoid` is the opposite of `guide`, not a weaker one — "not like this" is what
 * clients say most and what no tool records. The tone says so.
 */
const INTENT_TONE: Readonly<Record<ReferenceIntent, 'info' | 'neutral' | 'warn'>> = {
  match: 'info',
  guide: 'neutral',
  avoid: 'warn',
};

const SCOPE_OPTIONS = REFERENCE_SCOPES.map((value) => ({ value, label: SCOPE_LABEL[value] }));
const INTENT_OPTIONS = REFERENCE_INTENTS.map((value) => ({ value, label: INTENT_LABEL[value] }));

export interface ReferenceBoardProps {
  readonly projectId: string;
  /**
   * The render style the review is run against. The board is scope-based, so
   * "what applies" is a question about a specific view — without one there is
   * nothing to answer.
   */
  readonly presets: readonly { readonly id: string; readonly label: string }[];
  readonly className?: string;
}

export function ReferenceBoard({
  projectId,
  presets,
  className,
}: ReferenceBoardProps): JSX.Element {
  const board = useReferenceStore(selectBoard(projectId));
  const unannotated = useReferenceStore(selectUnannotatedCount(projectId));
  const loading = useReferenceStore((s) => s.loading);
  const error = useReferenceStore((s) => s.error);
  const review = useReferenceStore((s) => s.review);
  const reviewing = useReferenceStore((s) => s.reviewing);
  const load = useReferenceStore((s) => s.load);
  const add = useReferenceStore((s) => s.add);
  const runReview = useReferenceStore((s) => s.review_);

  const fileInput = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [presetId, setPresetId] = useState(presets[0]?.id ?? '');

  useEffect(() => {
    void load(projectId);
  }, [load, projectId]);

  const onFiles = useCallback(
    async (files: FileList | null) => {
      if (files === null || files.length === 0) return;
      setUploading(true);
      // Sequential, not Promise.all: the server's per-firm upload limit is a real
      // limit, and a parallel burst from one file picker would spend it on one
      // architect's drag-and-drop.
      for (const file of Array.from(files)) {
        await add(projectId, file);
      }
      setUploading(false);
      if (fileInput.current !== null) fileInput.current.value = '';
    },
    [add, projectId],
  );

  const presetOptions = useMemo(
    () => presets.map((p) => ({ value: p.id, label: p.label })),
    [presets],
  );

  return (
    <section className={cn('rounded-lg border border-line bg-surface', className)}>
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">Inspiration board</h2>
          <p className="text-2xs text-ink-muted">
            The pictures your client sent, and what each one is for. Renders read this.
          </p>
        </div>
        {unannotated > 0 ? <Badge tone="warn">{unannotated} not yet described</Badge> : null}
        <input
          ref={fileInput}
          type="file"
          accept="image/png,image/jpeg"
          multiple
          className="hidden"
          onChange={(event) => void onFiles(event.target.files)}
          data-testid="reference-file-input"
        />
        <Button
          size="sm"
          variant="secondary"
          disabled={uploading}
          onClick={() => fileInput.current?.click()}
        >
          {uploading ? <Spinner size={14} /> : null}
          Add pictures
        </Button>
      </header>

      {error !== null ? (
        <p className="border-b border-line px-4 py-2 text-2xs text-fail" role="alert">
          {error}
        </p>
      ) : null}

      {loading && board.length === 0 ? (
        <p className="px-4 py-6 text-2xs text-ink-muted">Loading the board…</p>
      ) : board.length === 0 ? (
        <p className="px-4 py-6 text-2xs text-ink-muted">
          Nothing pinned yet. Add the pictures your client sent — then say what each one is for, so
          renders can use them.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {board.map((reference) => (
            <ReferenceCard key={reference.id} projectId={projectId} reference={reference} />
          ))}
        </ul>
      )}

      {board.length > 0 && presetOptions.length > 0 ? (
        <div className="border-t border-line px-4 py-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex-1 min-w-[12rem]">
              <span className="mb-1 block text-2xs uppercase tracking-wide text-ink-muted">
                Check the board against
              </span>
              <Select
                value={presetId}
                onValueChange={setPresetId}
                options={presetOptions}
                aria-label="Render style to check the board against"
              />
            </label>
            <Button
              size="sm"
              variant="secondary"
              disabled={reviewing || presetId === ''}
              onClick={() => void runReview(projectId, presetId)}
            >
              {reviewing ? <Spinner size={14} /> : null}
              Check before rendering
            </Button>
          </div>
          {review !== null ? <ReviewPanel /> : null}
        </div>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// One card: the picture and the four answers
// ---------------------------------------------------------------------------

function ReferenceCard({
  projectId,
  reference,
}: {
  projectId: string;
  reference: ProjectReference;
}): JSX.Element {
  const annotate = useReferenceStore((s) => s.annotate);
  const remove = useReferenceStore((s) => s.remove);
  const [why, setWhy] = useState(reference.why);
  const [ignore, setIgnore] = useState(reference.ignore);
  const [label, setLabel] = useState(reference.label);

  // The server's copy is the truth. When a write lands (or another tab changes
  // it) the row is replaced, and these drafts follow rather than shadowing it.
  useEffect(() => setWhy(reference.why), [reference.why]);
  useEffect(() => setIgnore(reference.ignore), [reference.ignore]);
  useEffect(() => setLabel(reference.label), [reference.label]);

  const unanswered = reference.why === '' && reference.ignore === '';
  const commit = useCallback(
    (patch: Parameters<typeof annotate>[2]) => void annotate(projectId, reference.id, patch),
    [annotate, projectId, reference.id],
  );

  return (
    <li className="flex flex-wrap gap-4 px-4 py-4 sm:flex-nowrap">
      <div className="w-full sm:w-40 shrink-0">
        {reference.imageUrl != null && reference.imageUrl !== '' ? (
          <img
            src={reference.imageUrl}
            alt={reference.label}
            className="h-28 w-full rounded border border-line object-cover"
            loading="lazy"
          />
        ) : (
          <div className="grid h-28 w-full place-items-center rounded border border-line text-2xs text-ink-muted">
            No preview
          </div>
        )}
        <p className="mt-1 text-2xs text-ink-muted garh-nums">
          {reference.widthPx}×{reference.heightPx}
        </p>
      </div>

      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            onBlur={() => {
              const next = label.trim();
              // A blank name is refused by the server. Snapping back beats a
              // silent 422 the architect never sees.
              if (next === '') setLabel(reference.label);
              else if (next !== reference.label) commit({ label: next });
            }}
            aria-label="Reference name"
            className="min-w-0 flex-1 border-b border-transparent bg-transparent text-sm font-medium hover:border-line focus:border-brand focus:outline-none"
          />
          <Badge tone={INTENT_TONE[reference.intent]}>{INTENT_LABEL[reference.intent]}</Badge>
          {unanswered ? <Badge tone="warn">Not described</Badge> : null}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void remove(projectId, reference.id)}
            aria-label={`Remove ${reference.label}`}
          >
            Remove
          </Button>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-2xs uppercase tracking-wide text-ink-muted">
              Where it applies
            </span>
            <Select
              value={reference.scope}
              onValueChange={(scope) => commit({ scope })}
              options={SCOPE_OPTIONS}
              aria-label={`Where ${reference.label} applies`}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-2xs uppercase tracking-wide text-ink-muted">
              How hard to push it
            </span>
            <Select
              value={reference.intent}
              onValueChange={(intent) => commit({ intent })}
              options={INTENT_OPTIONS}
              aria-label={`How hard to push ${reference.label}`}
            />
          </label>
        </div>

        <label className="block">
          <span className="mb-1 block text-2xs uppercase tracking-wide text-ink-muted">
            What to take from it
          </span>
          <Textarea
            rows={2}
            value={why}
            maxLength={400}
            placeholder="the walnut cabinet fronts and the brass handles"
            onChange={(event) => setWhy(event.target.value)}
            onBlur={() => {
              if (why !== reference.why) commit({ why });
            }}
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-2xs uppercase tracking-wide text-ink-muted">
            What to leave out
          </span>
          <Textarea
            rows={2}
            value={ignore}
            maxLength={400}
            placeholder="the island — our plan has none"
            onChange={(event) => setIgnore(event.target.value)}
            onBlur={() => {
              if (ignore !== reference.ignore) commit({ ignore });
            }}
          />
        </label>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// The review: what applies, what does not, and what to settle first
// ---------------------------------------------------------------------------

function ReviewPanel(): JSX.Element | null {
  const review = useReferenceStore((s) => s.review);
  if (review === null) return null;

  return (
    <div
      className="mt-3 space-y-3 rounded border border-line bg-canvas p-3"
      data-testid="reference-review"
    >
      {review.conflicts.length === 0 ? (
        <p className="flex items-center gap-2 text-2xs text-ink-muted">
          <Icon name="check-circle" size={14} />
          Nothing to settle — {review.applies.length} reference
          {review.applies.length === 1 ? '' : 's'} will steer this render.
        </p>
      ) : (
        <ul className="space-y-2">
          {review.conflicts.map((conflict) => (
            <li
              key={`${conflict.kind}:${conflict.referenceIds.join(',')}`}
              className="flex gap-2 text-2xs"
            >
              <Icon
                name={conflict.kind === 'competing' ? 'alert-triangle' : 'alert-circle'}
                size={14}
                className="mt-px shrink-0 text-warn"
              />
              <span className="min-w-0">
                <span className="block">{conflict.question}</span>
                {/* Always stated: a question with an unknown default is dismissed. */}
                <span className="block text-ink-muted">If you do nothing: {conflict.default}</span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {review.notInView.length > 0 ? (
        <p className="text-2xs text-ink-muted">
          Not used in this view: {review.notInView.map((r) => r.label).join(', ')}
        </p>
      ) : null}

      {review.positive !== '' || review.negative !== '' ? (
        <details className="text-2xs">
          <summary className="cursor-pointer text-ink-muted">What the render will be told</summary>
          <dl className="mt-2 space-y-1">
            {review.positive !== '' ? (
              <>
                <dt className="text-ink-muted">Draw</dt>
                <dd className="font-mono">{review.positive}</dd>
              </>
            ) : null}
            {review.negative !== '' ? (
              <>
                <dt className="text-ink-muted">Avoid</dt>
                <dd className="font-mono">{review.negative}</dd>
              </>
            ) : null}
          </dl>
        </details>
      ) : null}
    </div>
  );
}
