/**
 * HatchBindingPanel.tsx — the surface, its material, the hatch that follows,
 * and the override.
 *
 * THE HONEST FRAME. This panel is about what the DRAWINGS print, not about
 * what the 3D view looks like — it is the counterpart to `MaterialsPanel`,
 * which says the opposite in its own header. Assigning "Exposed brick" in that
 * panel is what makes this one say BRICK; picking a pattern here changes only
 * the poché.
 *
 * SCOPE. Same ladder as a material assignment: building-wide by default, the
 * active storey when asked. Per-element overrides exist in `resolve.ts` and
 * have no UI yet, exactly as per-element material assignment does not.
 *
 * WHAT IS NOT WIRED, SAID OUT LOUD. The resolved pattern does not yet reach
 * `POST /projects/:id/sheets/generate` — the payload has no field for it, and
 * adding one is an API change outside this feature (the handoff in `index.ts`
 * spells it out, and `hatchPlan()` already builds the exact array to send).
 * Until then this panel is the truth about what the mapping decides, and the
 * sections still poché from their hardcoded constants. The panel says so,
 * once, rather than implying a change the sheets have not seen.
 */

import { useEffect, useMemo, useState } from 'react';

import { Button, cn } from '@garh/ui';

import { SURFACE_GROUPS, type SurfaceGroup, type SurfaceGroupRef } from '@garh/model';

import { useMaterialsCatalogue } from '../canvas/materials';
import { useModelStore } from '../../stores/model';
import { useUiStore } from '../../stores/ui';
import { HatchPatternPicker } from './HatchPatternPicker';
import { HatchSwatch } from './HatchSwatch';
import { hatchPattern, type HatchPatternKey } from './patterns';
import { SURFACE_LABELS, resolveHatch, type HatchOverrides } from './resolve';
import { useHatchOverrideStore } from './store';

export interface HatchBindingPanelProps {
  readonly className?: string | undefined;
}

type Scope = 'building' | 'storey';

export function HatchBindingPanel({ className }: HatchBindingPanelProps): JSX.Element {
  const catalogue = useMaterialsCatalogue();
  const house = useModelStore((s) => s.doc.house);
  const projectId = useModelStore((s) => s.projectId);
  const activeStoreyId = useUiStore((s) => s.activeStoreyId);

  const overrides = useHatchOverrideStore((s) => s.overrides);
  const setOverride = useHatchOverrideStore((s) => s.setOverride);
  const clearOverride = useHatchOverrideStore((s) => s.clearOverride);
  const bindProject = useHatchOverrideStore((s) => s.bindProject);

  const [group, setGroup] = useState<SurfaceGroup>('external_wall');
  const [scope, setScope] = useState<Scope>('building');

  // Overrides are per project. Without this a pattern chosen on one house
  // would follow the architect into the next one they opened.
  useEffect(() => {
    if (projectId !== null) bindProject(projectId);
  }, [projectId, bindProject]);

  const storeyScoped = scope === 'storey' && activeStoreyId !== null;
  const target: SurfaceGroupRef = useMemo(
    () => ({
      group,
      storeyId: storeyScoped ? activeStoreyId : null,
      elementId: null,
    }),
    [group, storeyScoped, activeStoreyId],
  );

  const resolved = resolveHatch({
    materials: house.materials,
    catalog: catalogue.index,
    overrides,
    group,
    ctx: { storeyId: target.storeyId, elementId: null },
  });

  // What the material WOULD give, so the grid can mark it even while an
  // override is in force — an architect should see what they are overriding.
  const implied = resolveHatch({
    materials: house.materials,
    catalog: catalogue.index,
    overrides: EMPTY_OVERRIDES,
    group,
    ctx: { storeyId: target.storeyId, elementId: null },
  });

  return (
    <aside
      className={cn('flex h-full w-full flex-col overflow-y-auto bg-surface', className)}
      aria-label="Hatch patterns"
    >
      <header className="sticky top-0 z-10 border-b border-line bg-surface px-3 py-2.5">
        <h2 className="text-sm font-semibold text-ink">Hatch patterns</h2>
        <p className="text-2xs leading-4 text-ink-subtle">
          Poché for sections and details. Materials imply a hatch; pick one here to override it.
        </p>
      </header>

      <div className="flex flex-wrap gap-1 px-3 pt-3" role="tablist" aria-label="Surface">
        {SURFACE_GROUPS.map((name) => (
          <Button
            key={name}
            size="sm"
            variant={name === group ? 'secondary' : 'ghost'}
            role="tab"
            aria-selected={name === group}
            onClick={() => {
              setGroup(name);
            }}
          >
            {SURFACE_LABELS[name]}
          </Button>
        ))}
      </div>

      <div className="flex items-center gap-1 px-3 pt-2">
        <span className="text-2xs font-medium text-ink-muted">Apply to</span>
        <Button
          size="sm"
          variant={scope === 'building' ? 'secondary' : 'ghost'}
          onClick={() => {
            setScope('building');
          }}
        >
          Whole building
        </Button>
        <Button
          size="sm"
          variant={scope === 'storey' ? 'secondary' : 'ghost'}
          disabled={activeStoreyId === null}
          onClick={() => {
            setScope('storey');
          }}
        >
          This storey
        </Button>
      </div>

      {/* What this surface draws right now, and on whose authority. */}
      <div className="flex items-start gap-2 px-3 pt-3">
        <HatchSwatch
          pattern={resolved.pattern}
          size={44}
          label={null}
          className="rounded-sm border border-line"
        />
        <div className="min-w-0">
          <p className="text-xs font-medium text-ink" data-testid="resolved-pattern">
            {hatchPattern(resolved.pattern).label}
          </p>
          <p className="text-2xs leading-4 text-ink-subtle" data-testid="resolved-why">
            {resolved.why}
          </p>
        </div>
      </div>

      <div className="px-3 py-3">
        <HatchPatternPicker
          value={resolved.pattern}
          implied={implied.source === 'material' ? implied.pattern : null}
          label={`Hatch pattern for ${SURFACE_LABELS[group].toLowerCase()}`}
          onChange={(pattern: HatchPatternKey) => {
            setOverride(target, pattern);
          }}
        />
      </div>

      <footer className="mt-auto border-t border-line px-3 py-2.5">
        <Button
          size="sm"
          variant="ghost"
          disabled={resolved.source !== 'override'}
          onClick={() => {
            clearOverride(target);
          }}
        >
          {implied.source === 'material' ? 'Follow the material' : 'Back to the surface default'}
        </Button>
      </footer>
    </aside>
  );
}

/** One shared empty map, so the "what would the material give" call allocates nothing. */
const EMPTY_OVERRIDES: HatchOverrides = new Map();
