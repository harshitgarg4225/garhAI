/**
 * The catalogue browser — searchable, grouped, filtered by the selected room.
 *
 * Two ways to place, because architects split roughly evenly between them:
 *
 *   **Drag** an item onto the canvas. The canvas core reads the payload with
 *   `readFurnitureDragPayload` (see `dnd.ts`) and calls `dropAt`.
 *   **Click** an item to arm the tool (the F shortcut arms it too), then click
 *   on the plan. Arming keeps the item loaded after each placement, so six
 *   dining chairs cost six clicks.
 *
 * Every dimension shown here goes through `lib/units`, so a project set to
 * metres never sees an inch (golden rule 6). Every item that had to assume its
 * clearance says so on the row — golden rule 4, assumptions are visible.
 */

import { useMemo, useState, type DragEvent } from 'react';

import { ROOM_TYPE_LABELS, type RoomType, type UnitsDisplay } from '@garh/model';
import { Chip, EmptyState, Icon, Input, SkeletonText, Tooltip, cn, type IconName } from '@garh/ui';

import {
  filterByRoomType,
  formatItemClearance,
  formatItemFootprint,
  groupByCategory,
  searchItems,
} from './catalogue';
import { setFurnitureDragPayload } from './dnd';
import { CATEGORY_COLOR } from './render';
import type { CatalogueItem, FurnitureCategory } from './types';
import { useFurniturePlacement } from './useFurniturePlacement';

const CATEGORY_ICON: Readonly<Record<FurnitureCategory, IconName>> = {
  bed: 'home',
  seating: 'sofa',
  table: 'grid',
  storage: 'layers',
  kitchen: 'cube',
  sanitary: 'shield',
  appliance: 'refresh',
  vehicle: 'compass',
  service: 'lightbulb',
  other: 'folder',
};

/** Stable empty list, so the memos below do not rerun on every render. */
const EMPTY_ITEMS: readonly CatalogueItem[] = [];

export interface FurnitureBrowserProps {
  /**
   * Override the room filter. Omit and the panel follows the canvas selection,
   * which is what an architect expects: click the master bedroom, see beds.
   */
  readonly roomType?: RoomType | null | undefined;
  readonly className?: string | undefined;
}

export function FurnitureBrowser({ roomType, className }: FurnitureBrowserProps): JSX.Element {
  const { catalogue, selectedRoomType, unitsDisplay, armedItem, arm } = useFurniturePlacement();
  const [query, setQuery] = useState('');
  const [filterOn, setFilterOn] = useState(true);

  const effectiveRoom = roomType === undefined ? selectedRoomType : roomType;
  const activeRoomFilter = filterOn ? effectiveRoom : null;

  const items = catalogue.loadable.state === 'ready' ? catalogue.loadable.data : EMPTY_ITEMS;

  const groups = useMemo(() => {
    const scoped = filterByRoomType(items, activeRoomFilter);
    return groupByCategory(searchItems(scoped, query));
  }, [items, activeRoomFilter, query]);

  const total = useMemo(() => groups.reduce((sum, group) => sum + group.items.length, 0), [groups]);

  return (
    <section
      aria-label="Furniture catalogue"
      className={cn('flex min-h-0 flex-col gap-3', className)}
    >
      <header className="flex flex-col gap-2 px-1">
        <Input
          type="search"
          value={query}
          iconLeft="search"
          placeholder="Search furniture…"
          aria-label="Search the furniture catalogue"
          onChange={(e) => {
            setQuery(e.currentTarget.value);
          }}
        />

        {effectiveRoom === null ? null : (
          <div className="flex flex-wrap items-center gap-2">
            <Chip
              size="sm"
              severity={filterOn ? 'brand' : 'neutral'}
              selected={filterOn}
              icon="filter"
              onClick={() => {
                setFilterOn((on) => !on);
              }}
              title={
                filterOn
                  ? 'Showing what normally goes in this room. Click to show everything.'
                  : 'Showing the whole catalogue. Click to narrow to this room.'
              }
            >
              {ROOM_TYPE_LABELS[effectiveRoom]}
            </Chip>
            <span className="text-xs text-ink-muted">
              {total} {total === 1 ? 'item' : 'items'}
            </span>
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {catalogue.loadable.state === 'loading' ? <SkeletonText lines={8} /> : null}

        {catalogue.loadable.state === 'error' ? (
          <EmptyState
            size="sm"
            icon="alert-triangle"
            title="Could not load the furniture list"
            description={catalogue.loadable.error.action}
            action={{ label: 'Try again', onClick: catalogue.reload }}
            demoAction={{
              notApplicable: 'The catalogue is reference data — there is no demo version of it.',
            }}
          />
        ) : null}

        {catalogue.loadable.state === 'ready' && total === 0 ? (
          <EmptyState
            size="sm"
            icon="search"
            title="Nothing matches"
            description={
              activeRoomFilter === null
                ? 'Try a shorter search — "bed", "sofa", "wc".'
                : 'Nothing in this room’s usual list matches. Turn the room filter off to see everything.'
            }
            action={
              activeRoomFilter === null
                ? {
                    label: 'Clear search',
                    onClick: () => {
                      setQuery('');
                    },
                  }
                : {
                    label: 'Show everything',
                    onClick: () => {
                      setFilterOn(false);
                    },
                  }
            }
            demoAction={{ notApplicable: 'Searching the catalogue needs no demo data.' }}
          />
        ) : null}

        {groups.map((group) => (
          <div key={group.category} className="mb-4">
            <h3 className="mb-1.5 flex items-center gap-1.5 px-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">
              <Icon name={CATEGORY_ICON[group.category]} size={13} />
              {group.label}
            </h3>
            <ul className="flex flex-col gap-1">
              {group.items.map((item) => (
                <li key={item.id}>
                  <FurnitureRow
                    item={item}
                    armed={armedItem?.id === item.id}
                    unitsDisplay={unitsDisplay}
                    onArm={() => {
                      arm(item);
                    }}
                  />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <footer className="border-t border-line px-2 py-1.5 text-xs text-ink-subtle">
        Drag onto the plan, or click to load it and click where it goes.{' '}
        <span className="whitespace-nowrap">R rotates 90°.</span>
      </footer>
    </section>
  );
}

interface FurnitureRowProps {
  readonly item: CatalogueItem;
  readonly armed: boolean;
  readonly unitsDisplay: UnitsDisplay;
  readonly onArm: () => void;
}

function FurnitureRow({ item, armed, unitsDisplay, onArm }: FurnitureRowProps): JSX.Element {
  const onDragStart = (event: DragEvent<HTMLButtonElement>): void => {
    setFurnitureDragPayload(event.dataTransfer, item.id);
  };

  return (
    <button
      type="button"
      draggable
      onDragStart={onDragStart}
      onClick={onArm}
      aria-pressed={armed}
      className={cn(
        'flex w-full cursor-grab items-center gap-2.5 rounded-md border px-2 py-1.5 text-left transition-colors active:cursor-grabbing',
        armed ? 'border-brand bg-brand-soft' : 'border-transparent hover:bg-surface-muted',
      )}
    >
      <span
        aria-hidden
        className="h-7 w-7 shrink-0 rounded-sm border border-line"
        style={{ backgroundColor: CATEGORY_COLOR[item.category] }}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-ink">{item.name}</span>
        <span className="block truncate text-xs text-ink-muted garh-nums">
          {formatItemFootprint(item, unitsDisplay)}
        </span>
      </span>
      {item.clearanceMm > 0 ? (
        <Tooltip
          content={
            item.clearanceAssumed
              ? `Assumed ${formatItemClearance(item, unitsDisplay)} — the catalogue did not send a clearance for this item.`
              : `Needs ${formatItemClearance(item, unitsDisplay)} in front to be usable.`
          }
        >
          <Chip size="sm" severity={item.clearanceAssumed ? 'info' : 'neutral'}>
            {formatItemClearance(item, unitsDisplay)}
          </Chip>
        </Tooltip>
      ) : null}
    </button>
  );
}
