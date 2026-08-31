/**
 * BriefForm — the structured half of F2 brief capture.
 *
 * Sections: household & floors · bedrooms (per-bedroom bath + preferences) ·
 * rooms & spaces (kitchen type, living/dining, the optional-room list) ·
 * style (the two launch kits + reference-image slot) · budget (band, exact ₹,
 * editable ₹/sq ft rate → derived area target).
 *
 * Every control dispatches ONE `brief.update` op through {@link useBrief}
 * (golden rule 1 — the op is the atom; §15 — everything undoable). Rooms are
 * written back as a whole normalised array because RFC 7386 replaces arrays
 * wholesale; scalars are single-key merge patches.
 *
 * The one honest stub: the reference-image SLOT stores only the chosen file's
 * name. Uploading the image and feeding it to the facade generator is Phase 5
 * (facade kits) — the slot says so instead of pretending.
 */

import { useMemo, useRef, useState } from 'react';

import { formatGaj, formatRupees, formatSqft, type Direction8, type JsonValue } from '@garh/model';
import {
  AssumptionChip,
  Card,
  CardHeader,
  Chip,
  Icon,
  IconButton,
  SelectField,
  SkeletonForm,
  SkeletonRegion,
  cn,
  type SelectOption,
} from '@garh/ui';

import { AreaField, CountStepper, RupeeField, ToggleField } from './fields';
import {
  BUDGET_BANDS,
  COUNTABLE_ROOM_TYPES,
  DEFAULT_RATE_PER_SQFT_INR,
  KITCHEN_TYPES,
  KITCHEN_TYPE_LABELS,
  OPTIONAL_ROOM_TYPES,
  RATE_ASSUMPTION_REASON,
  STYLE_KITS,
  addBedroom,
  areaTargetMm2,
  bandForBudget,
  bedroomRows,
  parseRupees,
  removeBedroom,
  roomCount,
  roomTypeLabel,
  setRoomCount,
  updateBedroom,
  withLivingDining,
  type BudgetBandId,
  type KitchenType,
  type LivingDining,
  type RoomRequest,
} from './types';
import { useBrief } from './useBrief';

export interface BriefFormProps {
  readonly className?: string | undefined;
}

// ---------------------------------------------------------------------------
// Small shared bits
// ---------------------------------------------------------------------------

/** Rooms array → JSON for a merge patch (arrays replace wholesale, RFC 7386). */
function roomsPatchValue(rooms: readonly RoomRequest[]): JsonValue {
  return rooms as unknown as JsonValue;
}

const roomLabel = roomTypeLabel;

const AI_DECIDES = '__ai__';

const FACING_OPTIONS: readonly SelectOption<string>[] = [
  { value: AI_DECIDES, label: 'AI decides' },
  ...(['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const).map((d) => ({
    value: d,
    label: d,
  })),
];

const STOREY_OPTIONS: readonly SelectOption<string>[] = [
  { value: '1', label: 'Ground only' },
  { value: '2', label: 'G+1' },
  { value: '3', label: 'G+2' },
  { value: '4', label: 'G+3' },
];

const FLOOR_NAMES = ['Ground', 'First', 'Second', 'Third'] as const;

function floorOptions(storeys: number | undefined): SelectOption<string>[] {
  const n = Math.max(1, Math.min(storeys ?? 2, FLOOR_NAMES.length));
  const out: SelectOption<string>[] = [{ value: AI_DECIDES, label: 'AI decides' }];
  for (let i = 0; i < n; i += 1) {
    out.push({ value: String(i), label: `${FLOOR_NAMES[i] ?? String(i)} floor` });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Per-room preferences (the "size OR AI decides / floor / facing / adjacency")
// ---------------------------------------------------------------------------

interface RoomPrefsEditorProps {
  readonly room: RoomRequest;
  readonly storeys: number | undefined;
  /** Room-type keys offered as "next to" wishes. */
  readonly adjacencyOptions: readonly string[];
  readonly onPatch: (patch: Partial<RoomRequest>) => void;
}

function RoomPrefsEditor({
  room,
  storeys,
  adjacencyOptions,
  onPatch,
}: RoomPrefsEditorProps): JSX.Element {
  const wishes = room.adjacentTo ?? [];
  const toggleWish = (key: string): void => {
    const next = wishes.includes(key) ? wishes.filter((w) => w !== key) : [...wishes, key];
    onPatch({ adjacentTo: next });
  };

  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <AreaField
        label="Target size"
        valueMm2={room.targetAreaMm2 ?? null}
        onCommit={(mm2) => onPatch({ targetAreaMm2: mm2 })}
      />
      <SelectField
        label="Floor"
        value={room.floor == null ? AI_DECIDES : String(room.floor)}
        options={floorOptions(storeys)}
        onValueChange={(v) => onPatch({ floor: v === AI_DECIDES ? null : Number(v) })}
      />
      <SelectField
        label="Facing"
        value={room.facing ?? AI_DECIDES}
        options={FACING_OPTIONS}
        onValueChange={(v) => onPatch({ facing: v === AI_DECIDES ? null : (v as Direction8) })}
      />
      {adjacencyOptions.length > 0 ? (
        <div className="sm:col-span-3">
          <span className="mb-1.5 block text-xs font-medium text-ink-muted">Next to (wishes)</span>
          <div className="flex flex-wrap gap-1.5">
            {adjacencyOptions.map((key) => (
              <Chip
                key={key}
                size="sm"
                icon={null}
                severity={wishes.includes(key) ? 'brand' : 'neutral'}
                selected={wishes.includes(key)}
                onClick={() => toggleWish(key)}
              >
                {roomLabel(key)}
              </Chip>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The form
// ---------------------------------------------------------------------------

export function BriefForm({ className }: BriefFormProps): JSX.Element {
  const { data, ready, update } = useBrief();
  const rooms = data.rooms ?? [];
  const bedrooms = bedroomRows(rooms);
  const [openPrefs, setOpenPrefs] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const adjacencyOptions = useMemo(() => {
    const present = new Set<string>(rooms.map((r) => r.type));
    present.add('staircase');
    present.delete('bedroom');
    present.delete('bedroom_master');
    return [...present].sort();
  }, [rooms]);

  if (!ready) {
    return (
      <SkeletonRegion label="Loading the brief" className={cn('space-y-5', className)}>
        <SkeletonForm rows={6} />
      </SkeletonRegion>
    );
  }

  const patchRooms = (next: readonly RoomRequest[], label: string): void => {
    update({ patch: { rooms: roomsPatchValue(next) }, label });
  };

  const rate = data.ratePerSqftInr ?? DEFAULT_RATE_PER_SQFT_INR;
  const target = areaTargetMm2(data.budgetInr, rate);

  return (
    <div className={cn('space-y-5', className)}>
      {/* ── Household & floors ─────────────────────────────────────────── */}
      <Card>
        <CardHeader
          title="Household & floors"
          description="Who lives here and how tall the house gets."
        />
        <div className="grid gap-x-6 gap-y-4 px-4 py-4 sm:grid-cols-2">
          <CountStepper
            label="Family size"
            value={data.familySize}
            min={1}
            max={30}
            hint="People living here day to day."
            onChange={(n) => update({ patch: { familySize: n }, label: 'Family size updated' })}
          />
          <SelectField
            label="Floors"
            value={data.storeys === undefined ? '' : String(data.storeys)}
            placeholder="Not decided"
            options={STOREY_OPTIONS}
            hint="G+1 means ground plus one floor."
            onValueChange={(v) =>
              update({ patch: { storeys: Number(v) }, label: 'Floor count updated' })
            }
          />
          <CountStepper
            label="Car parking"
            value={data.parkingCount}
            min={0}
            max={6}
            hint="Covered or stilt spaces to plan for."
            onChange={(n) => update({ patch: { parkingCount: n }, label: 'Parking updated' })}
          />
          <div className="flex flex-col justify-center">
            <ToggleField
              label="Stilt floor"
              hint="Parking under the house; living floors start above."
              value={data.hasStilt}
              onChange={(v) => update({ patch: { hasStilt: v }, label: 'Stilt updated' })}
            />
            <ToggleField
              label="Terrace access"
              hint="A stair to the terrace adds a mumty on the roof."
              value={data.terraceAccess}
              onChange={(v) =>
                update({ patch: { terraceAccess: v }, label: 'Terrace access updated' })
              }
            />
            <ToggleField
              label="Rainwater harvesting"
              hint="Required by every city pack we ship. Declare it and the warning clears."
              value={data.rainwaterHarvesting}
              onChange={(v) =>
                update({ patch: { rainwaterHarvesting: v }, label: 'Rainwater harvesting updated' })
              }
            />
            <ToggleField
              label="Future expansion"
              hint="Plan structure and stair for one more floor later."
              value={data.futureExpansion}
              onChange={(v) =>
                update({ patch: { futureExpansion: v }, label: 'Future expansion updated' })
              }
            />
          </div>
        </div>
      </Card>

      {/* ── Bedrooms ───────────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          title="Bedrooms"
          description="The first bedroom is the master. Give each one a bath and, if you like, a size, floor and facing."
          actions={
            <Chip size="sm" icon={null} severity="neutral">
              {bedrooms.length} bedroom{bedrooms.length === 1 ? '' : 's'}
            </Chip>
          }
        />
        {bedrooms.length === 0 ? (
          <div className="px-4 py-6 text-center">
            <p className="text-sm text-ink-muted">
              No bedrooms yet — this is the single biggest input the plan generator needs.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {bedrooms.map((room, i) => {
              const key = `bedroom-${i}`;
              const open = openPrefs === key;
              return (
                <li key={key} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="min-w-28 text-sm font-medium text-ink">
                      Bedroom {i + 1}
                      {room.type === 'bedroom_master' ? (
                        <span className="ml-1.5 text-2xs font-semibold uppercase tracking-wide text-brand-ink">
                          Master
                        </span>
                      ) : null}
                    </span>
                    <SelectField
                      label={`Bath for bedroom ${i + 1}`}
                      labelHidden
                      value={room.bath ?? 'common'}
                      options={[
                        { value: 'attached', label: 'Attached bath' },
                        { value: 'common', label: 'Common bath' },
                      ]}
                      fieldClassName="w-40"
                      onValueChange={(v) =>
                        patchRooms(
                          updateBedroom(rooms, i, { bath: v }),
                          `Bedroom ${i + 1} bath updated`,
                        )
                      }
                    />
                    <button
                      type="button"
                      className="garh-focus-ring inline-flex items-center gap-1 rounded-sm text-xs text-ink-muted hover:text-ink"
                      aria-expanded={open}
                      onClick={() => setOpenPrefs(open ? null : key)}
                    >
                      <Icon name={open ? 'chevron-up' : 'chevron-down'} size={13} />
                      Preferences
                    </button>
                    <span className="ml-auto">
                      <IconButton
                        label={`Remove bedroom ${i + 1}`}
                        icon="trash"
                        size="sm"
                        onClick={() => patchRooms(removeBedroom(rooms, i), 'Bedroom removed')}
                      />
                    </span>
                  </div>
                  {open ? (
                    <div className="mt-3">
                      <RoomPrefsEditor
                        room={room}
                        storeys={data.storeys}
                        adjacencyOptions={adjacencyOptions}
                        onPatch={(patch) =>
                          patchRooms(updateBedroom(rooms, i, patch), `Bedroom ${i + 1} updated`)
                        }
                      />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        <div className="border-t border-line px-4 py-3">
          <button
            type="button"
            className="garh-focus-ring inline-flex items-center gap-1.5 rounded-md text-sm font-medium text-brand-ink hover:underline"
            onClick={() => patchRooms(addBedroom(rooms), 'Bedroom added')}
          >
            <Icon name="plus" size={14} />
            Add a bedroom
          </button>
        </div>
      </Card>

      {/* ── Rooms & spaces ─────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          title="Rooms & spaces"
          description="Kitchen, living and the extras. Each one can carry its own preferences."
        />
        <div className="grid gap-x-6 gap-y-4 px-4 py-4 sm:grid-cols-2">
          <SelectField
            label="Kitchen"
            value={data.kitchenType ?? ''}
            placeholder="Pick a kitchen type"
            options={KITCHEN_TYPES.map((k) => ({ value: k, label: KITCHEN_TYPE_LABELS[k] }))}
            onValueChange={(v) =>
              update({
                patch: {
                  kitchenType: v as KitchenType,
                  rooms: roomsPatchValue(setRoomCount(rooms, 'kitchen', 1)),
                },
                label: 'Kitchen updated',
              })
            }
          />
          <SelectField
            label="Living & dining"
            value={data.livingDining ?? ''}
            placeholder="Combined or separate?"
            options={[
              { value: 'combined', label: 'Combined living/dining' },
              { value: 'separate', label: 'Separate living and dining' },
            ]}
            onValueChange={(v) =>
              update({
                patch: {
                  livingDining: v as LivingDining,
                  rooms: roomsPatchValue(withLivingDining(rooms, v as LivingDining)),
                },
                label: 'Living/dining updated',
              })
            }
          />
        </div>
        <ul className="divide-y divide-line border-t border-line">
          {OPTIONAL_ROOM_TYPES.map((type) => {
            const count = roomCount(rooms, type);
            const enabled = count > 0;
            const entry = rooms.find((r) => r.type === type);
            const open = openPrefs === type;
            return (
              <li key={type} className="px-4 py-2.5">
                <div className="flex flex-wrap items-center gap-3">
                  {COUNTABLE_ROOM_TYPES.has(type) ? (
                    <>
                      <span className="min-w-28 text-sm text-ink">{roomLabel(type)}</span>
                      <CountStepper
                        label={`${roomLabel(type)} count`}
                        labelHidden
                        value={count === 0 ? undefined : count}
                        emptyText="None"
                        min={0}
                        max={4}
                        onChange={(n) =>
                          patchRooms(setRoomCount(rooms, type, n), `${roomLabel(type)} updated`)
                        }
                      />
                    </>
                  ) : (
                    <ToggleField
                      label={roomLabel(type)}
                      value={enabled ? true : undefined}
                      className="min-w-52 flex-1 py-0"
                      onChange={(v) =>
                        patchRooms(
                          setRoomCount(rooms, type, v ? 1 : 0),
                          `${roomLabel(type)} ${v ? 'added' : 'removed'}`,
                        )
                      }
                    />
                  )}
                  {enabled ? (
                    <button
                      type="button"
                      className="garh-focus-ring ml-auto inline-flex items-center gap-1 rounded-sm text-xs text-ink-muted hover:text-ink"
                      aria-expanded={open}
                      onClick={() => setOpenPrefs(open ? null : type)}
                    >
                      <Icon name={open ? 'chevron-up' : 'chevron-down'} size={13} />
                      Preferences
                    </button>
                  ) : null}
                </div>
                {enabled && open && entry !== undefined ? (
                  <div className="mt-3">
                    <RoomPrefsEditor
                      room={entry}
                      storeys={data.storeys}
                      adjacencyOptions={adjacencyOptions.filter((k) => k !== type)}
                      onPatch={(patch) =>
                        patchRooms(
                          setRoomCount(rooms, type, count, { ...entry, ...patch }),
                          `${roomLabel(type)} updated`,
                        )
                      }
                    />
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </Card>

      {/* ── Style ──────────────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          title="Style"
          description="Two launch styles. The facade generator applies your pick as editable 3D geometry."
        />
        <div className="grid gap-3 px-4 py-4 sm:grid-cols-2" role="radiogroup" aria-label="Style">
          {STYLE_KITS.map((kit) => {
            const active = data.styleKitId === kit.id;
            return (
              <button
                key={kit.id}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() =>
                  update({ patch: { styleKitId: kit.id }, label: `Style set to ${kit.name}` })
                }
                className={cn(
                  'garh-focus-ring rounded-lg border p-3 text-left transition-colors',
                  active
                    ? 'border-brand bg-brand-soft'
                    : 'border-line bg-surface hover:border-ink-subtle',
                )}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-ink">{kit.name}</span>
                  {active ? (
                    <Icon name="check-circle" size={16} className="text-brand-ink" />
                  ) : null}
                </span>
                <span className="mt-1 block text-xs leading-5 text-ink-muted">{kit.blurb}</span>
              </button>
            );
          })}
        </div>
        <div className="border-t border-line px-4 py-3">
          <span className="mb-1.5 block text-xs font-medium text-ink-muted">
            Reference image (optional)
          </span>
          {data.styleReferenceName == null ? (
            <button
              type="button"
              className="garh-focus-ring flex w-full items-center justify-center gap-2 rounded-md border border-dashed border-line-strong px-3 py-4 text-xs text-ink-muted hover:border-ink-subtle hover:text-ink"
              onClick={() => fileInputRef.current?.click()}
            >
              <Icon name="image" size={15} />
              Choose a photo the client likes
            </button>
          ) : (
            <Chip
              icon="image"
              severity="neutral"
              onRemove={() =>
                update({ patch: { styleReferenceName: null }, label: 'Reference image removed' })
              }
              removeLabel="Remove reference image"
            >
              {data.styleReferenceName}
            </Chip>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            aria-hidden="true"
            tabIndex={-1}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file !== undefined) {
                update({
                  patch: { styleReferenceName: file.name },
                  label: 'Reference image noted',
                });
              }
              e.target.value = '';
            }}
          />
          <p className="mt-1.5 text-2xs leading-4 text-ink-subtle">
            We note the file for now; uploading it and using it to steer the facade options arrives
            with facades in Phase 5.
          </p>
        </div>
      </Card>

      {/* ── Budget ─────────────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          title="Budget"
          description="The budget and the rate together set the built-up area the plans aim for."
        />
        <div className="grid gap-x-6 gap-y-4 px-4 py-4 sm:grid-cols-2">
          <SelectField
            label="Budget band"
            value={data.budgetBand ?? ''}
            placeholder="Pick a band"
            options={BUDGET_BANDS.map((b) => ({ value: b.id, label: b.label }))}
            hint="Picking a band fills a starting figure you can refine."
            onValueChange={(v) => {
              const band = BUDGET_BANDS.find((b) => b.id === (v as BudgetBandId));
              if (band === undefined) return;
              update({
                patch: { budgetBand: band.id, budgetInr: band.midInr },
                label: 'Budget band updated',
              });
            }}
          />
          <RupeeField
            label="Construction budget"
            value={data.budgetInr ?? null}
            hint="Shorthand works: 45L, 1.2Cr."
            onCommit={(rupees) =>
              update({
                patch: { budgetInr: rupees, budgetBand: bandForBudget(rupees) },
                label: 'Budget updated',
              })
            }
          />
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
          <AssumptionChip
            label="Rate"
            valueText={`${formatRupees(rate)} / sq ft`}
            reason={RATE_ASSUMPTION_REASON}
            accepted={data.ratePerSqftInr !== undefined}
            onCommit={(raw) => {
              const parsed = parseRupees(raw.replace(/\/?\s*sq\s*ft\.?$/i, ''));
              if (parsed === null || parsed <= 0) return;
              update({ patch: { ratePerSqftInr: parsed }, label: 'Rate updated' });
            }}
          />
          {target === null ? (
            <span className="text-xs text-ink-subtle">
              Give a budget to see the area target this rate implies.
            </span>
          ) : (
            <span className="text-xs text-ink-muted">
              Area target:{' '}
              <span className="font-semibold text-ink garh-nums">
                {formatSqft(target, 0)} · {formatGaj(target)}
              </span>{' '}
              built-up
            </span>
          )}
        </div>
      </Card>
    </div>
  );
}

export default BriefForm;
