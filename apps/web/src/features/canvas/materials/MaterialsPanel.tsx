/**
 * MaterialsPanel.tsx — pick a surface group, pick a material, dispatch op 29.
 *
 * THE HONEST FRAME (§15, and the facade-isolation rule from §8): materials
 * recolour the 3D view and feed the render prompts later — they never move a
 * wall, change an area, or touch compliance. The panel says so, once, at the
 * top, so nobody wonders why the plan did not change.
 *
 * SCOPE. Assignments are building-wide by default; the storey toggle narrows
 * the target to the active storey (`SurfaceGroupRef.storeyId`). Per-element
 * assignment exists in the op and the resolver (`resolve.ts` rank 2) but has
 * no UI in Phase 5 — it arrives with 3D element selection.
 *
 * Swatches are procedural colour chips from the catalogue's `colorHex`
 * (inherited fact 4: no texture binaries, nothing for the asset gate to
 * catch). Dispatch goes through the model store — the ONLY writer — with an
 * undo label, so a material change is one ⌘Z like everything else.
 */

import { useCallback, useMemo, useState } from 'react';

import { Button, Spinner, cn } from '@garh/ui';

import type { Op, SurfaceGroup, SurfaceGroupRef } from '@garh/model';
import type { MaterialItem } from '../../../lib/schemas';
import { useModelStore } from '../../../stores/model';
import { useUiStore } from '../../../stores/ui';
import { materialAssignOp, materialClearOp } from './assignOps';
import { resolveAssignment, swatchHex } from './resolve';
import { materialsForPick, SURFACE_PICKS, type SurfacePick } from './surfaceGroups';
import { useMaterialsCatalogue } from './useMaterialsCatalogue';

export interface MaterialsPanelProps {
  className?: string | undefined;
}

type Scope = 'building' | 'storey';

export function MaterialsPanel({ className }: MaterialsPanelProps): JSX.Element {
  const catalogue = useMaterialsCatalogue();
  const house = useModelStore((s) => s.doc.house);
  const activeStoreyId = useUiStore((s) => s.activeStoreyId);

  const [group, setGroup] = useState<SurfaceGroup>('external_wall');
  const [scope, setScope] = useState<Scope>('building');

  const pick = SURFACE_PICKS.find((p) => p.group === group) ?? (SURFACE_PICKS[0] as SurfacePick);

  const storeyScoped = scope === 'storey' && activeStoreyId !== null;
  const target: SurfaceGroupRef = useMemo(
    () => ({
      group: pick.group,
      // Id<'storey'>'s brand is optional (ids.ts) — a plain string assigns.
      storeyId: storeyScoped ? activeStoreyId : null,
      elementId: null,
    }),
    [pick.group, storeyScoped, activeStoreyId],
  );

  // What resolves at this scope. The panel edits ONE row (the exact target),
  // but it also says when the storey is inheriting a building-wide choice.
  const cascade = resolveAssignment(house.materials, pick.group, {
    storeyId: storeyScoped ? activeStoreyId : null,
    elementId: null,
  });
  const exactMatch =
    cascade !== null && cascade.target.storeyId === (storeyScoped ? activeStoreyId : null);
  const currentId = exactMatch && cascade !== null ? cascade.materialId : null;
  const inheritedId = !exactMatch && cascade !== null ? cascade.materialId : null;

  const dispatch = useCallback((op: Op | null, label: string): void => {
    if (op === null) return;
    const result = useModelStore.getState().dispatch([op], { label, source: 'manual' });
    if (result.ok) return;
    useUiStore.getState().pushToast({
      tone: 'warning',
      title: result.issues[0]?.message ?? 'That material assignment is not valid.',
      // `?? null`: ToastInput.description is `string | null` and does not admit
      // an explicit undefined under exactOptionalPropertyTypes.
      description: result.issues[0]?.fix ?? null,
      dedupeKey: 'materials-rejected',
    });
  }, []);

  const assign = (item: MaterialItem): void => {
    dispatch(
      materialAssignOp(house, target, item.id),
      `${item.name} on ${pick.label.toLowerCase()}`,
    );
  };

  const clear = (): void => {
    dispatch(materialClearOp(house, target), `${pick.label} back to default`);
  };

  return (
    <aside
      className={cn('flex h-full w-full flex-col overflow-y-auto bg-surface', className)}
      aria-label="Materials"
    >
      <header className="sticky top-0 z-10 border-b border-line bg-surface px-3 py-2.5">
        <h2 className="text-sm font-semibold text-ink">Materials</h2>
        <p className="text-2xs leading-4 text-ink-subtle">
          Recolours the 3D view only — plans, areas and compliance never change.
        </p>
      </header>

      {/* Surface group picker */}
      <div className="flex flex-wrap gap-1 px-3 pt-3" role="tablist" aria-label="Surface group">
        {SURFACE_PICKS.map((p) => (
          <Button
            key={p.group}
            size="sm"
            variant={p.group === pick.group ? 'secondary' : 'ghost'}
            role="tab"
            aria-selected={p.group === pick.group}
            onClick={() => setGroup(p.group)}
          >
            {p.label}
          </Button>
        ))}
      </div>
      <p className="px-3 pt-1 text-2xs text-ink-subtle">{pick.hint}</p>

      {/* Scope */}
      <div className="flex items-center gap-1 px-3 pt-2">
        <span className="text-2xs font-medium text-ink-muted">Apply to</span>
        <Button
          size="sm"
          variant={scope === 'building' ? 'secondary' : 'ghost'}
          onClick={() => setScope('building')}
        >
          Whole building
        </Button>
        <Button
          size="sm"
          variant={scope === 'storey' ? 'secondary' : 'ghost'}
          disabled={activeStoreyId === null}
          onClick={() => setScope('storey')}
        >
          This storey
        </Button>
      </div>
      {scope === 'storey' && activeStoreyId === null ? (
        <p className="px-3 pt-1 text-2xs text-ink-subtle">
          No storey is active — pick one in the storey tabs first.
        </p>
      ) : null}

      {inheritedId !== null ? (
        <p className="px-3 pt-1 text-2xs text-ink-subtle">
          Inheriting the whole-building choice — picking here overrides it for this storey.
        </p>
      ) : null}

      {/* Material list */}
      <div className="flex flex-col gap-1 px-3 py-3">
        <PanelBody
          catalogue={catalogue}
          pick={pick}
          currentId={currentId}
          onAssign={assign}
        />
      </div>

      <footer className="mt-auto border-t border-line px-3 py-2.5">
        <Button size="sm" variant="ghost" disabled={currentId === null} onClick={clear}>
          Back to default
        </Button>
      </footer>
    </aside>
  );
}

// ---------------------------------------------------------------------------

interface PanelBodyProps {
  catalogue: ReturnType<typeof useMaterialsCatalogue>;
  pick: SurfacePick;
  currentId: string | null;
  onAssign: (item: MaterialItem) => void;
}

function PanelBody({ catalogue, pick, currentId, onAssign }: PanelBodyProps): JSX.Element {
  if (catalogue.loadable.state === 'loading') {
    return (
      <div className="flex items-center gap-2 py-4 text-xs text-ink-muted">
        <Spinner /> Loading the material catalogue…
      </div>
    );
  }
  if (catalogue.loadable.state === 'error') {
    return (
      <div className="flex flex-col items-start gap-2 py-4">
        <p className="text-xs text-ink-muted">
          The material catalogue did not load — {catalogue.loadable.error.message}
        </p>
        <Button size="sm" variant="secondary" onClick={catalogue.reload}>
          Try again
        </Button>
      </div>
    );
  }

  const items = materialsForPick(catalogue.loadable.data, pick);
  if (items.length === 0) {
    return (
      <p className="py-4 text-xs text-ink-muted">
        No catalogue material declares itself for {pick.label.toLowerCase()} yet. Other groups
        still work.
      </p>
    );
  }

  return (
    <>
      {currentId === null ? (
        <p className="pb-1 text-2xs text-ink-subtle">
          Wearing the default — pick a material to change it. One click, one undo step.
        </p>
      ) : null}
      {items.map((item) => {
        const selected = item.id === currentId;
        return (
          <button
            key={item.id}
            type="button"
            className={cn(
              'garh-focus-ring flex w-full items-center gap-2 rounded border px-2 py-1.5 text-left',
              selected ? 'border-ink bg-surface-muted' : 'border-line hover:bg-surface-muted',
            )}
            aria-pressed={selected}
            onClick={() => onAssign(item)}
          >
            <span
              aria-hidden
              className="h-5 w-5 shrink-0 rounded-sm border border-line"
              style={{ backgroundColor: swatchHex(item) }}
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs text-ink">{item.name}</span>
              <span className="block truncate text-2xs text-ink-subtle">{item.category}</span>
            </span>
            {selected ? <span className="text-2xs font-medium text-ink">Applied</span> : null}
          </button>
        );
      })}
    </>
  );
}
