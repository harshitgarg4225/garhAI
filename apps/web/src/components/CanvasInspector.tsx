/**
 * CanvasInspector — the right rail's content on the two canvas tabs, Phase 5
 * edition. Composition of three feature panels behind one lazy boundary:
 *
 *   InspectorPanel          (overlays)  the Phase-4 element inspector
 *   FacadeComponentPanel    (facade)    op-28 editing for ONE kit component
 *   MaterialsPanel          (materials) op-29 surface-group assignment
 *
 * ROUTING RULE (the facade module's stated integration contract): when the
 * PRIMARY selection parses as a `facadecomp` id, the selection panel is the
 * facade component's editor — in BOTH views, because selection survives the
 * Tab switch and an inspector that answered "unknown element" for something
 * you just clicked would be the rail lying about the selection it shows.
 *
 * The materials panel rides along only on the 3D tab: op 29 is rendered
 * colour, and colour on a drafting plan is noise. It sits below the selection
 * panel rather than replacing it — assigning a material and inspecting an
 * element are not modes, they happen together.
 *
 * WHY THIS FILE IS THE LAZY BOUNDARY: `ProjectShell` renders on every tab,
 * including Brief; these three panels live under `features/canvas/**`, and
 * importing them eagerly would pull the canvas layer (and with it `three`)
 * into the shell's chunk. The shell lazy-loads THIS component for canvas tabs
 * only — same reasoning, same mechanism as the Phase-4 InspectorPanel note.
 */

import type { HouseModel, UnitsDisplay } from '@garh/model';
import { tryParseId } from '@garh/model';

import { FacadeComponentPanel } from '../features/canvas/facade/FacadeComponentPanel';
import { MaterialsPanel } from '../features/canvas/materials/MaterialsPanel';
import { InspectorPanel } from '../features/canvas/overlays/inspector/InspectorPanel';

export interface CanvasInspectorProps {
  readonly house: HouseModel;
  readonly selectedIds: readonly string[];
  readonly display: UnitsDisplay;
  /** True on the 3D tab — adds the materials panel. */
  readonly threeD: boolean;
}

export function CanvasInspector({
  house,
  selectedIds,
  display,
  threeD,
}: CanvasInspectorProps): JSX.Element {
  const primaryId = selectedIds[0] ?? null;
  const facadeComponentId =
    primaryId !== null && tryParseId(primaryId)?.type === 'facadecomp' ? primaryId : null;

  const selectionPanel =
    facadeComponentId !== null ? (
      <FacadeComponentPanel componentId={facadeComponentId} display={display} />
    ) : (
      <InspectorPanel house={house} selectedIds={selectedIds} display={display} />
    );

  if (!threeD) return selectionPanel;

  return (
    <div className="flex h-full w-full flex-col bg-surface">
      <div className="min-h-0 flex-1 overflow-y-auto">{selectionPanel}</div>
      <div className="max-h-[45%] shrink-0 overflow-y-auto border-t border-line">
        <MaterialsPanel />
      </div>
    </div>
  );
}

export default CanvasInspector;
