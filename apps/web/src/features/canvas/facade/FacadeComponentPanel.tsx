/**
 * FacadeComponentPanel.tsx — the per-element facade inspector (§8 "per-element
 * edit": select a chajja, change its projection; select a railing, change its
 * kind).
 *
 * Every control commits ONE `facade.edit_component` op (op 28) — an RFC 7386
 * merge patch on the component's params. Undo works with no help from here
 * because the fold writes the inverse patch. Nothing on this panel can touch
 * a wall, an opening or a room: op 28's fold edits `facade.components[i]` and
 * nothing else, which is the §8 isolation invariant, and the footer says so
 * to the user.
 *
 * MOUNTING (integrator contract): render this INSTEAD of the generic
 * `InspectorPanel` body when the primary selection's id parses to element
 * type `facadecomp` (`tryParseId(id)?.type === 'facadecomp'`) — equivalently,
 * when the recorded pick kind is `FACADE_PICK_KIND`.
 */

import { useCallback } from 'react';

import { LengthInput, Select, cn } from '@garh/ui';

import type {
  FacadeComponent,
  FacadeComponentId,
  FacadeComponentKind,
  JsonObject,
  Op,
  UnitsDisplay,
} from '@garh/model';

import { selectHouse, useModelStore } from '../../../stores/model';
import { useUiStore } from '../../../stores/ui';
import { editComponentOp } from './ops';
import { kitById } from './kits';
import { enumParam, intParam, strParam, RAILING_STYLES, type RailingStyle } from './types';

const KIND_LABEL: Readonly<Record<FacadeComponentKind, string>> = {
  window_trim: 'Window trim',
  chajja: 'Chajja',
  parapet_profile: 'Parapet profile',
  cladding_zone: 'Cladding zone',
  porch: 'Porch',
  railing: 'Railing',
  band: 'Band',
  louver: 'Louver',
  entry_feature: 'Entry feature',
};

const RAILING_LABEL: Readonly<Record<RailingStyle, string>> = {
  'ms-slim': 'MS slim',
  glass: 'Glass',
  masonry: 'Masonry',
};

export interface FacadeComponentPanelProps {
  readonly componentId: string;
  readonly display: UnitsDisplay;
  readonly className?: string | undefined;
}

export function FacadeComponentPanel({
  componentId,
  display,
  className,
}: FacadeComponentPanelProps): JSX.Element {
  const house = useModelStore(selectHouse);
  const component = house.facade.components.find((c) => c.id === componentId) ?? null;
  const kit = kitById(house.facade.kitId);

  const commit = useCallback(
    (patch: JsonObject, label: string): void => {
      const op: Op = editComponentOp(componentId as FacadeComponentId, patch);
      const result = useModelStore.getState().dispatch([op], { label, source: 'manual' });
      if (result.ok) return;
      useUiStore.getState().pushToast({
        tone: 'warning',
        title: result.issues[0]?.message ?? 'That facade edit is not valid.',
        // `?? null`: ToastInput.description is `string | null` and does not
        // admit an explicit undefined under exactOptionalPropertyTypes.
        description: result.issues[0]?.fix ?? null,
        dedupeKey: 'facade-edit-rejected',
      });
    },
    [componentId],
  );

  if (component === null) {
    // Reachable: the selection can outlive a kit re-apply that replaced ids.
    return (
      <aside className={cn('flex h-full w-full flex-col bg-surface', className)} aria-label="Facade element">
        <p className="px-3 py-6 text-xs text-ink-muted">
          This facade element no longer exists — applying or regenerating a kit replaces every
          element. Select one in the 3D view, or re-open the Facade panel.
        </p>
      </aside>
    );
  }

  return (
    <aside
      className={cn('flex h-full w-full flex-col overflow-y-auto bg-surface', className)}
      aria-label="Facade element properties"
    >
      <header className="sticky top-0 z-10 border-b border-line bg-surface px-3 py-2.5">
        <h2 className="truncate text-sm font-semibold text-ink">{KIND_LABEL[component.kind]}</h2>
        <p className="truncate text-xs text-ink-muted">
          {kit !== null ? `${kit.name} kit` : 'Facade element'}
        </p>
      </header>

      <div className="flex flex-col gap-3 px-3 py-3">
        <ComponentFields component={component} display={display} commit={commit} />
      </div>

      <footer className="mt-auto border-t border-line px-3 py-3">
        <p className="text-2xs leading-tight text-ink-subtle">
          Facade edits change only this element. Walls, rooms and compliance are never affected.
        </p>
      </footer>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Fields per component kind
// ---------------------------------------------------------------------------

interface FieldsProps {
  readonly component: FacadeComponent;
  readonly display: UnitsDisplay;
  readonly commit: (patch: JsonObject, label: string) => void;
}

function ComponentFields({ component, display, commit }: FieldsProps): JSX.Element {
  const p = component.params;

  switch (component.kind) {
    case 'chajja':
      return (
        <>
          <ProjectionSelect component={component} commit={commit} display={display} />
          <MmField
            label="Thickness"
            valueMm={intParam(p, 'thicknessMm', 100)}
            display={display}
            min={25}
            onCommit={(mm) => {
              commit({ thicknessMm: mm }, 'Chajja thickness');
            }}
          />
          <MmField
            label="Side overhang"
            valueMm={intParam(p, 'sideOverhangMm', 0)}
            display={display}
            min={0}
            onCommit={(mm) => {
              commit({ sideOverhangMm: mm }, 'Chajja overhang');
            }}
          />
        </>
      );

    case 'railing': {
      const style: RailingStyle = enumParam(p, 'style', RAILING_STYLES, 'ms-slim');
      return (
        <>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-ink-muted">Railing kind</span>
            <Select<RailingStyle>
              value={style}
              onValueChange={(v) => {
                commit({ style: v }, 'Railing kind');
              }}
              options={RAILING_STYLES.map((s) => ({ value: s, label: RAILING_LABEL[s] }))}
            />
          </label>
          <MmField
            label="Height"
            valueMm={intParam(p, 'heightMm', 1050)}
            display={display}
            min={600}
            onCommit={(mm) => {
              commit({ heightMm: mm }, 'Railing height');
            }}
          />
        </>
      );
    }

    case 'window_trim': {
      const style = strParam(p, 'style', 'flush-band');
      if (style === 'recessed') {
        return (
          <MmField
            label="Recess depth"
            valueMm={Math.abs(intParam(p, 'projectionMm', -75))}
            display={display}
            min={25}
            onCommit={(mm) => {
              commit({ projectionMm: -mm }, 'Window recess');
            }}
            hint="How deep the window sits behind the wall face."
          />
        );
      }
      return (
        <>
          <MmField
            label="Band width"
            valueMm={intParam(p, 'widthMm', 100)}
            display={display}
            min={40}
            onCommit={(mm) => {
              commit({ widthMm: mm }, 'Trim width');
            }}
          />
          <MmField
            label="Projection"
            valueMm={intParam(p, 'projectionMm', 40)}
            display={display}
            min={10}
            onCommit={(mm) => {
              commit({ projectionMm: mm }, 'Trim projection');
            }}
          />
        </>
      );
    }

    case 'cladding_zone':
      return (
        <>
          <MmField
            label="Band width"
            valueMm={intParam(p, 'widthMm', 1200)}
            display={display}
            min={300}
            onCommit={(mm) => {
              commit({ widthMm: mm }, 'Cladding width');
            }}
          />
          <MmField
            label="Position along wall"
            valueMm={intParam(p, 'offsetMm', 0)}
            display={display}
            min={0}
            onCommit={(mm) => {
              commit({ offsetMm: mm }, 'Cladding position');
            }}
            hint="Centre of the band, measured from the wall's start."
          />
        </>
      );

    case 'porch':
      return (
        <>
          <MmField
            label="Projection"
            valueMm={intParam(p, 'projectionMm', 1500)}
            display={display}
            min={300}
            onCommit={(mm) => {
              commit({ projectionMm: mm }, 'Porch projection');
            }}
          />
          <MmField
            label="Width"
            valueMm={intParam(p, 'widthMm', 1800)}
            display={display}
            min={900}
            onCommit={(mm) => {
              commit({ widthMm: mm }, 'Porch width');
            }}
          />
          <MmField
            label="Slab thickness"
            valueMm={intParam(p, 'thicknessMm', 150)}
            display={display}
            min={75}
            onCommit={(mm) => {
              commit({ thicknessMm: mm }, 'Porch thickness');
            }}
          />
        </>
      );

    case 'parapet_profile':
      return (
        <>
          <MmField
            label="Profile height"
            valueMm={intParam(p, 'heightMm', 1050)}
            display={display}
            min={300}
            onCommit={(mm) => {
              commit({ heightMm: mm }, 'Parapet profile height');
            }}
            hint="Visual dressing only — the compliance parapet height lives in Levels."
          />
          <MmField
            label="Cap thickness"
            valueMm={intParam(p, 'capThicknessMm', 75)}
            display={display}
            min={25}
            onCommit={(mm) => {
              commit({ capThicknessMm: mm }, 'Parapet cap');
            }}
          />
        </>
      );

    case 'band':
    case 'louver':
    case 'entry_feature':
      return (
        <p className="text-xs text-ink-muted">
          No editable parameters for this element kind yet.
        </p>
      );
  }
}

// A thin wrapper so every mm field shares the same clamp + LengthInput wiring.
function MmField({
  label,
  valueMm,
  display,
  min,
  onCommit,
  hint,
}: {
  readonly label: string;
  readonly valueMm: number;
  readonly display: UnitsDisplay;
  readonly min: number;
  readonly onCommit: (mm: number) => void;
  readonly hint?: string | undefined;
}): JSX.Element {
  return (
    <LengthInput
      label={label}
      valueMm={valueMm}
      display={display}
      bareUnit="mm"
      hint={hint}
      onCommitMm={(mm) => {
        onCommit(Math.max(mm, min));
      }}
    />
  );
}

// ---------------------------------------------------------------------------

/**
 * Chajja projection: a Select over the ACTIVE KIT's rule-allowed projections
 * (that is what "rule-allowed variants" means), with the current value kept
 * selectable even if a previous edit moved it off-list.
 */
function ProjectionSelect({
  component,
  commit,
  display,
}: {
  readonly component: FacadeComponent;
  readonly commit: (patch: JsonObject, label: string) => void;
  readonly display: UnitsDisplay;
}): JSX.Element {
  const house = useModelStore(selectHouse);
  const kit = kitById(house.facade.kitId);
  const allowed = kit?.components.chajja.allowedProjectionsMm ?? [600, 750];
  const current = intParam(component.params, 'projectionMm', 600);
  const values = allowed.includes(current) ? allowed : [...allowed, current];

  if (values.length <= 1) {
    return (
      <MmField
        label="Projection"
        valueMm={current}
        display={display}
        min={300}
        onCommit={(mm) => {
          commit({ projectionMm: mm }, 'Chajja projection');
        }}
      />
    );
  }

  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-muted">Projection</span>
      <Select
        value={String(current)}
        onValueChange={(v) => {
          const mm = Number(v);
          if (Number.isSafeInteger(mm) && mm > 0) commit({ projectionMm: mm }, 'Chajja projection');
        }}
        options={values.map((mm) => ({ value: String(mm), label: `${String(mm)} mm` }))}
      />
      <span className="mt-0.5 block text-2xs leading-tight text-ink-subtle">
        This kit allows {allowed.map((v) => `${String(v)} mm`).join(' or ')}.
      </span>
    </label>
  );
}
