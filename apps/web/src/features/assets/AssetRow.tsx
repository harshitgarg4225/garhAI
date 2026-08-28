/**
 * One row of the asset browser.
 *
 * `memo`'d, and that is not cargo cult: with the depth filter off this list can
 * hold 120 rows, and every keystroke changes the parent's state. Without the
 * memo, React re-renders 120 subtrees per character. With it — and with the two
 * callbacks below kept stable by the parent — it re-renders only the rows whose
 * record or pinned state actually changed, which for a keystroke is usually
 * none of the ones that survived.
 *
 * The row is two controls, not one: activating the row is a different action
 * from pinning it, and nesting a button inside a button is invalid HTML that
 * makes the inner one unreachable by keyboard in some browsers.
 *
 * DRAG. Furniture rows are draggable and write the SAME payload the canvas
 * already reads (`features/canvas/furniture/dnd.ts`,
 * `readFurnitureDragPayload`). Reusing that contract rather than inventing a
 * second MIME type is what makes a drag out of this panel land as a real
 * placement instead of doing nothing — the failure mode of a module that
 * believes it is registered and never calls the registry.
 */

import { memo, type DragEvent } from 'react';

import { Chip, Icon, Tooltip, cn, type IconName } from '@garh/ui';

import { formatDimensionPair, formatLengthDisplay, type UnitsDisplay } from '../../lib/units';
import { setFurnitureDragPayload } from '../canvas/furniture/dnd';
import { hasFootprint, roomTypeLabel, type AssetRecord } from './types';

/**
 * A glyph per category. Deliberately not imported from
 * `features/canvas/furniture/render.ts` — that module pulls the three.js proxy
 * cache in with it, and a text list has no business shipping a 3D renderer.
 */
const CATEGORY_ICON: Readonly<Record<string, IconName>> = {
  'furniture:bed': 'home',
  'furniture:seating': 'sofa',
  'furniture:table': 'grid',
  'furniture:storage': 'layers',
  'furniture:kitchen': 'cube',
  'furniture:sanitary': 'shield',
  'furniture:appliance': 'refresh',
  'furniture:vehicle': 'compass',
  'furniture:service': 'lightbulb',
  'furniture:other': 'folder',
  'material:floor': 'grid',
  'material:wall': 'wall',
  'material:roof': 'home',
  'material:glazing': 'window',
  'material:joinery': 'door',
  'material:railing': 'balcony',
  'material:other': 'folder',
};

export interface AssetRowProps {
  readonly record: AssetRecord;
  readonly favourite: boolean;
  readonly unitsDisplay: UnitsDisplay;
  /** Stable identity required — see the note at the top of this file. */
  readonly onToggleFavourite: (key: string) => void;
  /** Stable identity required. Called on click; the parent also records the use. */
  readonly onUse: (record: AssetRecord) => void;
}

export const AssetRow = memo(function AssetRow({
  record,
  favourite,
  unitsDisplay,
  onToggleFavourite,
  onUse,
}: AssetRowProps): JSX.Element {
  const draggable = record.kind === 'furniture';

  const onDragStart = (event: DragEvent<HTMLButtonElement>): void => {
    setFurnitureDragPayload(event.dataTransfer, record.id);
  };

  const footprint = hasFootprint(record)
    ? formatDimensionPair(record.widthMm, record.depthMm, unitsDisplay)
    : null;

  const subtitle =
    record.kind === 'furniture'
      ? record.roomTypes.length === 0
        ? record.categoryLabel
        : `${record.categoryLabel} · ${record.roomTypes.map(roomTypeLabel).join(', ')}`
      : record.surfaceGroups.length === 0
        ? record.categoryLabel
        : `${record.categoryLabel} · ${record.surfaceGroups.join(', ')}`;

  return (
    <li className="flex items-center gap-1">
      <button
        type="button"
        draggable={draggable}
        onDragStart={draggable ? onDragStart : undefined}
        onClick={() => {
          onUse(record);
        }}
        title={subtitle}
        className={cn(
          'flex min-w-0 flex-1 items-center gap-2.5 rounded-md border border-transparent px-2 py-1.5 text-left transition-colors hover:bg-surface-muted',
          draggable && 'cursor-grab active:cursor-grabbing',
        )}
      >
        {record.swatchHex === null ? (
          <span
            aria-hidden
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm border border-line bg-surface-muted text-ink-muted"
          >
            <Icon name={CATEGORY_ICON[record.categoryKey] ?? 'folder'} size={14} />
          </span>
        ) : (
          <span
            aria-hidden
            className="h-7 w-7 shrink-0 rounded-sm border border-line"
            style={{ backgroundColor: record.swatchHex }}
          />
        )}

        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-ink">{record.name}</span>
          <span className="block truncate text-xs text-ink-muted garh-nums">
            {footprint === null ? subtitle : `${record.categoryLabel} · ${footprint}`}
          </span>
        </span>

        {record.clearanceMm !== null && record.clearanceMm > 0 ? (
          <Tooltip
            content={
              record.clearanceAssumed
                ? `Assumed ${formatLengthDisplay(record.clearanceMm, unitsDisplay)} of access — the catalogue did not send a clearance for this item.`
                : `Needs ${formatLengthDisplay(record.clearanceMm, unitsDisplay)} in front to be usable.`
            }
          >
            <Chip size="sm" severity={record.clearanceAssumed ? 'info' : 'neutral'}>
              {formatLengthDisplay(record.clearanceMm, unitsDisplay)}
            </Chip>
          </Tooltip>
        ) : null}
      </button>

      <button
        type="button"
        aria-pressed={favourite}
        aria-label={favourite ? `Unpin ${record.name}` : `Pin ${record.name}`}
        title={favourite ? 'Remove from favourites' : 'Keep in favourites'}
        onClick={() => {
          onToggleFavourite(record.key);
        }}
        className={cn(
          'shrink-0 rounded-md p-1.5 transition-colors',
          favourite ? 'text-brand' : 'text-ink-subtle hover:text-ink',
        )}
      >
        <Icon name="pin" size={15} />
      </button>
    </li>
  );
});
