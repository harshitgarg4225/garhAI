/**
 * MaterialsPanel.tsx — pick a surface group, pick a material, dispatch op 29.
 *
 * THE HONEST FRAME (§15, and the facade-isolation rule from §8): materials
 * recolour the 3D view and feed the render prompts later — they never move a
 * wall, change an area, or touch compliance. The panel says so, once, at the
 * top, so nobody wonders why the plan did not change.
 *
 * SCOPE. Assignments are building-wide by default; the storey toggle narrows
 * the target to the active storey (`SurfaceGroupRef.storeyId`), and — since
 * 2026-09-20 — "This element" narrows it to the ONE element selected in the
 * canvas (`SurfaceGroupRef.elementId`, `resolve.ts` rank 2). That is what
 * makes "granite on THIS wall" possible; the op and the resolver always
 * supported it, and the 3D scene already splits an element-scoped id into
 * its own bucket (`elementScopedAssignmentIds`), so the click-to-select
 * Phase 5 shipped is all it was waiting for.
 *
 * The element scope is offered only when exactly one element is selected AND
 * it belongs to the surface group being edited: assigning "floors" to the
 * wall you have selected would write a row that can never resolve, and a
 * control that writes a no-op is worse than no control.
 *
 * Swatches are procedural colour chips from the catalogue's `colorHex`
 * (inherited fact 4: no texture binaries, nothing for the asset gate to
 * catch). Dispatch goes through the model store — the ONLY writer — with an
 * undo label, so a material change is one ⌘Z like everything else.
 */

import { useCallback, useMemo, useState } from 'react';

import { Button, Spinner, cn } from '@garh/ui';

import { tryParseId, type Op, type SurfaceGroup, type SurfaceGroupRef } from '@garh/model';
import type { MaterialItem } from '../../../lib/schemas';
import { useModelStore } from '../../../stores/model';
import { useSelectionStore } from '../../../stores/selection';
import { useUiStore } from '../../../stores/ui';
import { materialAssignOp, materialClearOp } from './assignOps';
import { resolveAssignment, swatchHex } from './resolve';
import {
  materialsForPick,
  SURFACE_PICKS,
  surfaceGroupOfElement,
  type SurfacePick,
} from './surfaceGroups';
import { useMaterialsCatalogue } from './useMaterialsCatalogue';

export interface MaterialsPanelProps {
  className?: string | undefined;
}

type Scope = 'building' | 'storey' | 'element';

export function MaterialsPanel({ className }: MaterialsPanelProps): JSX.Element {
  const catalogue = useMaterialsCatalogue();
  const house = useModelStore((s) => s.doc.house);
  const activeStoreyId = useUiStore((s) => s.activeStoreyId);

  const [group, setGroup] = useState<SurfaceGroup>('external_wall');
  const [scope, setScope] = useState<Scope>('building');
  const selectedIds = useSelectionStore((s) => s.ids);

  const pick = SURFACE_PICKS.find((p) => p.group === group) ?? (SURFACE_PICKS[0] as SurfacePick);

  // The one selected element and the group it belongs to. A multi-select has
  // no single element to target, and an id the model no longer carries
  // resolves to null rather than writing a dangling assignment row.
  const selectedElement = useMemo(() => {
    if (selectedIds.length !== 1) return null;
    const id = selectedIds[0] ?? null;
    if (id === null || tryParseId(id) === null) return null;
    const elementGroup = surfaceGroupOfElement(house, id);
    return elementGroup === null ? null : { id, group: elementGroup };
  }, [house, selectedIds]);

  // Offered only where it can resolve: the selection's own group.
  const elementScopable = selectedElement !== null && selectedElement.group === pick.group;
  const elementScoped = scope === 'element' && elementScopable;
  const storeyScoped = scope === 'storey' && activeStoreyId !== null;
  const target: SurfaceGroupRef = useMemo(
    () => ({
      group: pick.group,
      // Id<'storey'>'s brand is optional (ids.ts) — a plain string assigns.
      storeyId: elementScoped ? null : storeyScoped ? activeStoreyId : null,
      elementId: elementScoped && selectedElement !== null ? selectedElement.id : null,
    }),
    [pick.group, elementScoped, storeyScoped, activeStoreyId, selectedElement],
  );

  // What resolves at this scope. The panel edits ONE row (the exact target),
  // but it also says when the storey is inheriting a building-wide choice.
  const cascade = resolveAssignment(house.materials, pick.group, {
    storeyId: elementScoped ? null : storeyScoped ? activeStoreyId : null,
    elementId: target.elementId,
  });
  const exactMatch =
    cascade !== null &&
    cascade.target.elementId === target.elementId &&
    cascade.target.storeyId === target.storeyId;
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
      elementScoped
        ? `${item.name} on this ${pick.label.replace(/s$/, '').toLowerCase()}`
        : `${item.name} on ${pick.label.toLowerCase()}`,
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
        <Button
          size="sm"
          variant={scope === 'element' ? 'secondary' : 'ghost'}
          disabled={!elementScopable}
          title={
            elementScopable
              ? 'Paint only the element selected in the canvas'
              : 'Select one element of this kind in the canvas first'
          }
          onClick={() => setScope('element')}
        >
          This element
        </Button>
      </div>
      {scope === 'storey' && activeStoreyId === null ? (
        <p className="px-3 pt-1 text-2xs text-ink-subtle">
          No storey is active — pick one in the storey tabs first.
        </p>
      ) : null}
      {scope === 'element' && !elementScopable ? (
        <p className="px-3 pt-1 text-2xs text-ink-subtle">
          {selectedElement === null
            ? 'Select exactly one wall, opening, floor, stair or column in the canvas — then this paints only that one.'
            : `That selection is ${pick.label.toLowerCase() === 'floors' ? 'not a floor' : `not ${pick.label.toLowerCase()}`}; switch the surface above to match it.`}
        </p>
      ) : null}
      {elementScoped ? (
        <p className="px-3 pt-1 text-2xs text-ink-subtle">
          Painting one element only — it keeps this material when the building or storey choice
          changes.
        </p>
      ) : null}

      {inheritedId !== null ? (
        <p className="px-3 pt-1 text-2xs text-ink-subtle">
          {elementScoped
            ? 'Inheriting the wider choice — picking here overrides it for this element alone.'
            : 'Inheriting the whole-building choice — picking here overrides it for this storey.'}
        </p>
      ) : null}

      {/* Material list */}
      <div className="flex flex-col gap-1 px-3 py-3">
        <PanelBody catalogue={catalogue} pick={pick} currentId={currentId} onAssign={assign} />
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
        No catalogue material declares itself for {pick.label.toLowerCase()} yet. Other groups still
        work.
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
