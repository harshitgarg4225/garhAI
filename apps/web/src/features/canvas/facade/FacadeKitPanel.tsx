/**
 * FacadeKitPanel.tsx — apply/regenerate/clear facade kits (§8, §15).
 *
 * HONEST CONTROLS, per §15:
 *  - The seed is a visible, editable integer. "Shuffle" steps a documented
 *    LCG and SHOWS the new number; typing 7 always reproduces seed 7's
 *    facade. No hidden randomness anywhere on this panel.
 *  - Kit cards are live generator output (`KitThumbnail`) over the user's own
 *    frontage when one exists, the sample house otherwise — so an empty
 *    project still teaches what each kit does.
 *  - `kitFitIssues` states what the kit will skip on this plan (no entry
 *    door → no porch) BEFORE apply, instead of surprising after.
 *  - Applying replaces the facade sub-model and nothing else; the panel says
 *    so, because §8's isolation is a user-facing promise (facade churn cannot
 *    break the drawing set).
 *
 * Golden rule 1: every button dispatches ops through the model store. The
 * generator runs inside `applyKitOp` at click time; this component never
 * mutates anything.
 */

import { useCallback, useMemo, useState } from 'react';

import { Badge, Button, cn } from '@garh/ui';

import type { HouseModel, Op } from '@garh/model';

import { selectHouse, useModelStore } from '../../../stores/model';
import { useUiStore } from '../../../stores/ui';
import { KitThumbnail } from './KitThumbnail';
import { applyKitOp, clearFacadeOp } from './ops';
import { kitFitIssues } from './generator';
import { FACADE_KITS, kitById } from './kits';
import { hasFrontage } from './thumbnail';
import type { FacadeKitDef } from './types';
import { nextSeed } from './variation';

/** Seed shown before the user ever touches the control. Any value works. */
const DEFAULT_SEED = 7;

export interface FacadeKitPanelProps {
  readonly className?: string | undefined;
}

export function FacadeKitPanel({ className }: FacadeKitPanelProps): JSX.Element {
  const house = useModelStore(selectHouse);
  const activeKit = kitById(house.facade.kitId);

  const [seed, setSeed] = useState<number>(() =>
    activeKit !== null ? house.facade.seed : DEFAULT_SEED,
  );
  const [colorwayByKit, setColorwayByKit] = useState<Readonly<Record<string, string>>>(() =>
    activeKit !== null && house.facade.colorwayId !== null
      ? { [activeKit.id]: house.facade.colorwayId }
      : {},
  );

  const dispatch = useCallback((ops: readonly Op[], label: string): void => {
    const result = useModelStore.getState().dispatch(ops, { label, source: 'manual' });
    if (result.ok) return;
    useUiStore.getState().pushToast({
      tone: 'warning',
      title: result.issues[0]?.message ?? 'That facade change is not valid here.',
      // `?? null`: ToastInput.description is `string | null` and does not admit
      // an explicit undefined under exactOptionalPropertyTypes.
      description: result.issues[0]?.fix ?? null,
      dedupeKey: 'facade-rejected',
    });
  }, []);

  const apply = useCallback(
    (kit: FacadeKitDef): void => {
      const current = useModelStore.getState().doc.house;
      const colorwayId = colorwayByKit[kit.id] ?? kit.colorways[0]?.id ?? null;
      dispatch([applyKitOp(current, kit, seed, colorwayId)], `Apply ${kit.name} facade`);
    },
    [colorwayByKit, dispatch, seed],
  );

  const clear = useCallback((): void => {
    dispatch([clearFacadeOp()], 'Remove facade');
  }, [dispatch]);

  const shuffle = useCallback((): void => {
    setSeed((s) => nextSeed(s));
  }, []);

  const frontage = hasFrontage(house);
  const previewHouse = frontage ? house : null;

  return (
    <div className={cn('flex flex-col gap-4 px-3 py-3', className)} aria-label="Facade kits">
      {/* Current state, honestly */}
      {activeKit !== null ? (
        <div className="flex items-center justify-between gap-2 rounded-md border border-line bg-surface px-3 py-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-ink">{activeKit.name}</p>
            <p className="truncate text-xs text-ink-muted garh-nums">
              Seed {house.facade.seed}
              {house.facade.colorwayId !== null ? ` · ${house.facade.colorwayId}` : ''} ·{' '}
              {house.facade.components.length} elements
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              size="sm"
              onClick={() => {
                apply(activeKit);
              }}
            >
              Regenerate
            </Button>
            <Button size="sm" variant="ghost" onClick={clear}>
              Remove
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-xs text-ink-muted">
          A kit dresses your external walls with chajjas, trims, cladding and railings. It never
          moves a wall or a room — remove it any time.
        </p>
      )}

      {/* The seed — an honest, typed control */}
      <div className="flex items-end gap-2">
        <label className="block grow">
          <span className="mb-1 block text-xs font-medium text-ink-muted">Variation seed</span>
          <input
            type="number"
            min={0}
            step={1}
            value={seed}
            onChange={(e) => {
              const v = Math.floor(Number(e.target.value));
              if (Number.isSafeInteger(v) && v >= 0) setSeed(v);
            }}
            className="garh-focus-ring w-full rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink garh-nums"
          />
        </label>
        <Button size="sm" variant="secondary" onClick={shuffle}>
          Shuffle
        </Button>
      </div>
      <p className="-mt-2 text-2xs leading-tight text-ink-subtle">
        Same seed, same facade — type a number to get that exact variation back.
      </p>

      {/* Kit cards — previews generated from the generator itself */}
      <div className="flex flex-col gap-3">
        {FACADE_KITS.map((kit) => (
          <KitCard
            key={kit.id}
            kit={kit}
            house={house}
            previewHouse={previewHouse}
            seed={seed}
            active={activeKit?.id === kit.id}
            colorwayId={colorwayByKit[kit.id] ?? kit.colorways[0]?.id ?? null}
            onColorway={(id) => {
              setColorwayByKit((prev) => ({ ...prev, [kit.id]: id }));
            }}
            onApply={() => {
              apply(kit);
            }}
          />
        ))}
      </div>

      {!frontage ? (
        <p className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-muted">
          These previews use a sample house. Draw external walls in the plan — press{' '}
          <kbd className="rounded border border-line px-1">W</kbd> — and the cards will preview your
          own frontage instead.
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------

interface KitCardProps {
  readonly kit: FacadeKitDef;
  readonly house: HouseModel;
  readonly previewHouse: HouseModel | null;
  readonly seed: number;
  readonly active: boolean;
  readonly colorwayId: string | null;
  readonly onColorway: (id: string) => void;
  readonly onApply: () => void;
}

function KitCard({
  kit,
  house,
  previewHouse,
  seed,
  active,
  colorwayId,
  onColorway,
  onApply,
}: KitCardProps): JSX.Element {
  const issues = useMemo(() => kitFitIssues(house, kit), [house, kit]);
  const blocked = issues.some((i) => i.severity === 'blocker');

  return (
    <section
      className={cn(
        'overflow-hidden rounded-lg border bg-surface',
        active ? 'border-brand' : 'border-line',
      )}
      aria-label={`${kit.name} kit`}
    >
      <div className="aspect-[16/9] w-full bg-surface-sunken">
        <KitThumbnail
          kit={kit}
          seed={seed}
          colorwayId={colorwayId}
          house={previewHouse}
          className="h-full w-full"
        />
      </div>
      <div className="flex flex-col gap-2 px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <h3 className="truncate text-sm font-semibold text-ink">{kit.name}</h3>
          {active ? <Badge tone="pass">Applied</Badge> : null}
        </div>
        <p className="text-xs leading-snug text-ink-muted">{kit.description}</p>

        {/* Colorways: procedural swatches from kit data, no assets */}
        <div className="flex items-center gap-1.5" role="radiogroup" aria-label="Colorway">
          {kit.colorways.map((cw) => (
            <button
              key={cw.id}
              type="button"
              role="radio"
              aria-checked={cw.id === colorwayId}
              title={cw.name}
              onClick={() => {
                onColorway(cw.id);
              }}
              className={cn(
                'garh-focus-ring h-6 w-10 overflow-hidden rounded border',
                cw.id === colorwayId ? 'border-brand' : 'border-line',
              )}
            >
              <span className="flex h-full w-full">
                <span className="h-full w-1/2" style={{ backgroundColor: cw.base }} />
                <span className="h-full w-1/4" style={{ backgroundColor: cw.accent }} />
                <span className="h-full w-1/4" style={{ backgroundColor: cw.trim }} />
              </span>
            </button>
          ))}
        </div>

        {issues.length > 0 ? (
          <ul className="flex flex-col gap-1">
            {issues.map((issue) => (
              <li key={issue.text} className="text-2xs leading-tight text-ink-subtle">
                {issue.text}
              </li>
            ))}
          </ul>
        ) : null}

        <Button size="sm" variant="primary" disabled={blocked} onClick={onApply} fullWidth>
          {active ? `Re-apply ${kit.name}` : `Apply ${kit.name}`}
        </Button>
      </div>
    </section>
  );
}
