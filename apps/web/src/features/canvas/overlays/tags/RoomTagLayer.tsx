/**
 * RoomTagLayer.tsx — room name + area on the plan, click-to-edit, no overlaps.
 *
 * The rooms come from `packages/model/src/rooms.ts` via `house.rooms`. This
 * layer RENDERS them. It does not re-run planar subdivision, it does not
 * recompute areas, and it does not have an opinion about where a room is — if
 * it did, the canvas and the compliance engine would eventually disagree about
 * a bedroom's area and only one of them would be on the drawing.
 *
 * §15: "any dimension or area label on canvas is click-to-edit. No dead text."
 * Both lines are live:
 *   · the NAME picks as `{ kind: 'room', id }` → `room.assign`
 *   · the AREA picks as `{ kind: 'room', id }` too, but through a separate
 *     instance whose id carries the {@link AREA_HANDLE_SUFFIX}, so the page can
 *     tell "rename this" from "set a target for this" → `room.set_target`
 *
 * §14 is met the same way the dimension layer meets it: placement runs only
 * when the document changes or the zoom crosses a band (`shouldReplace`),
 * label groups are scaled imperatively, leader lines are one batched
 * `LineSegments`, and every pick is one instanced mesh in the core's registry.
 */

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Text } from '@react-three/drei';
import {
  BufferAttribute,
  BufferGeometry,
  Matrix4,
  PlaneGeometry,
  Quaternion,
  Vector3,
} from 'three';
import type { InstancedMesh, Object3D } from 'three';

import { useCanvasCore, usePickableInstances, WORLD_UNITS_PER_MM } from '../../core';
import { LineBuffer } from '../render/lines';
import {
  getOverlayMaterials,
  LABEL_FONT_SIZE_LOCAL,
  LABEL_FONT_URL,
  ROOM_LABEL_RENDER_ORDER,
} from '../render/overlayMaterials';
import { useScreenScale, useViewportEffect } from '../render/screenScale';
import { placeLabels, shouldReplace, type PlacedLabel } from './placement';
import {
  DEFAULT_TAG_STYLE,
  tagFitsOnScreen,
  tagsToPlaceable,
  type RoomTagVM,
  type TagStyle,
} from './tags';

/**
 * Appended to a room id to make the AREA line's pick handle.
 *
 * A suffix rather than a second `PickKind`: the core's kind table is a shared
 * contract with Phase 5 and it should describe what an element IS, not which of
 * its two labels you clicked. The page strips the suffix before it touches the
 * selection store, so nothing downstream ever sees a synthetic id.
 */
export const AREA_HANDLE_SUFFIX = '#area';

/** Split a room-tag pick back into "which room" and "which line". */
export function parseRoomTagHandle(id: string): { roomId: string; part: 'name' | 'area' } {
  return id.endsWith(AREA_HANDLE_SUFFIX)
    ? { roomId: id.slice(0, -AREA_HANDLE_SUFFIX.length), part: 'area' }
    : { roomId: id, part: 'name' };
}

export interface RoomTagLayerProps {
  /** Tags for the ACTIVE storey, from `roomTags(...)`. Memoise upstream. */
  tags: readonly RoomTagVM[];
  elevationMm?: number | undefined;
  storeyId?: string | null | undefined;
  /** Rooms drawn in the brand colour — usually the current selection. */
  highlightIds?: readonly string[] | undefined;
  style?: TagStyle | undefined;
  fontUrl?: string | undefined;
  visible?: boolean | undefined;
}

const scratchMatrix = /* @__PURE__ */ new Matrix4();
const scratchPosition = /* @__PURE__ */ new Vector3();
const scratchScale = /* @__PURE__ */ new Vector3(1, 1, 1);
const FLAT = /* @__PURE__ */ new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), -Math.PI / 2);

export function RoomTagLayer({
  tags,
  elevationMm = 0,
  storeyId = null,
  highlightIds,
  style = DEFAULT_TAG_STYLE,
  fontUrl = LABEL_FONT_URL,
  visible = true,
}: RoomTagLayerProps): JSX.Element | null {
  const core = useCanvasCore();
  const materials = getOverlayMaterials();
  const scale = useScreenScale(1);

  const highlighted = useMemo(() => new Set(highlightIds ?? []), [highlightIds]);

  /**
   * The placed layout.
   *
   * State, not a ref, because a new layout genuinely changes what React
   * renders (which labels are visible, where their leader lines go). The
   * §14 protection is that it is recomputed on a ZOOM BAND, not per frame:
   * `shouldReplace` gates it, so a continuous wheel gesture produces a handful
   * of re-layouts rather than sixty.
   */
  const [layout, setLayout] = useState<readonly PlacedLabel[]>([]);
  const placedAtMmPerPx = useRef(0);

  const replace = useRef<() => void>(() => undefined);
  replace.current = (): void => {
    const mmPerPx = core.viewport.mmPerPx;
    const visibleTags = tags.filter((tag) => tagFitsOnScreen(tag, mmPerPx, style));
    placedAtMmPerPx.current = mmPerPx;
    setLayout(placeLabels(tagsToPlaceable(visibleTags, mmPerPx, style)));
  };

  // Document change ⇒ always re-place.
  useEffect(() => {
    replace.current();
    // `tags` and `style` are the inputs; the zoom is handled below.
  }, [tags, style]);

  // Zoom change ⇒ re-place only when the band was crossed.
  useViewportEffect(() => {
    if (!shouldReplace(placedAtMmPerPx.current, core.viewport.mmPerPx)) return;
    replace.current();
  });

  const placedById = useMemo(() => new Map(layout.map((p) => [p.id, p])), [layout]);

  /**
   * Tags that got a place, paired with it. Built with a loop rather than
   * `filter().map()` so `placed` is non-optional: the pick instances are
   * written in this order, and an entry the update pass could skip would
   * desynchronise instance ids from rooms — click one room's area, edit
   * another's.
   */
  const items = useMemo(() => {
    const out: { tag: RoomTagVM; placed: PlacedLabel }[] = [];
    for (const tag of tags) {
      const placed = placedById.get(tag.roomId);
      if (placed !== undefined) out.push({ tag, placed });
    }
    return out;
  }, [tags, placedById]);

  // ── Leader lines: one batched geometry ───────────────────────────────────
  // Lazily initialised — see the note in `DimensionLayer`: `useRef(new X())`
  // constructs an X on every render and discards all but the first.
  const bufferRef = useRef<LineBuffer | null>(null);
  bufferRef.current ??= new LineBuffer(32);
  const geometryRef = useRef<BufferGeometry | null>(null);
  geometryRef.current ??= new BufferGeometry();
  useEffect(() => {
    const geometry = geometryRef.current;
    return () => geometry?.dispose();
  }, []);

  // ── Pick proxies: one instance per label LINE (name and area) ────────────
  const pickIds = useMemo(
    () => items.flatMap((item) => [item.tag.roomId, `${item.tag.roomId}${AREA_HANDLE_SUFFIX}`]),
    [items],
  );
  const pickRef = useRef<InstancedMesh | null>(null);
  const pickRegister = usePickableInstances('room', pickIds, storeyId);
  const pickGeometry = useMemo(() => new PlaneGeometry(1, 1), []);
  useEffect(() => () => pickGeometry.dispose(), [pickGeometry]);

  const labelRefs = useRef<(Object3D | null)[]>([]);
  labelRefs.current.length = items.length;

  /**
   * Position pass. Runs on every camera commit and after every re-layout.
   *
   * Positions are in world units and do not depend on zoom (the LAYOUT does,
   * and that is gated above) — but the leader-line buffer and the pick quads
   * are sized in pixels, so they are rewritten here. In place, no allocation.
   */
  useViewportEffect(() => {
    const buffer = bufferRef.current;
    const geometry = geometryRef.current;
    const pick = pickRef.current;
    if (buffer === null || geometry === null) return;

    buffer.begin();
    const grew = buffer.reserve(items.length);

    let instance = 0;
    items.forEach((item, index) => {
      const placed = item.placed;
      const group = labelRefs.current[index] ?? null;
      if (group !== null) {
        group.position.set(
          placed.atMm.x * WORLD_UNITS_PER_MM,
          elevationMm * WORLD_UNITS_PER_MM,
          -placed.atMm.y * WORLD_UNITS_PER_MM,
        );
        // Flat on the plan. `items` already excludes every tag the placer or
        // the legibility threshold dropped, so anything reaching here is meant
        // to be on screen — an overflow label is DIMMED (see `fillOpacity`
        // below), never hidden. A room whose name you cannot read is worse
        // than one drawn slightly too close to its neighbour.
        group.rotation.set(-Math.PI / 2, 0, 0);
        group.visible = true;
      }

      const leader = placed.leaderMm;
      if (leader !== null) {
        buffer.push(leader[0].x, leader[0].y, leader[1].x, leader[1].y, elevationMm);
      }

      if (pick !== null) {
        const halfW = placed.halfWidthMm;
        const halfH = placed.halfHeightMm;
        // Two stacked quads: the name occupies the top half of the label box,
        // the area the bottom. That is where the two lines of text actually
        // are, so the click target and the thing you clicked agree.
        for (const part of [0, 1]) {
          const cy = placed.atMm.y + (part === 0 ? halfH / 2 : -halfH / 2);
          scratchPosition.set(
            placed.atMm.x * WORLD_UNITS_PER_MM,
            elevationMm * WORLD_UNITS_PER_MM,
            -cy * WORLD_UNITS_PER_MM,
          );
          scratchScale.set(halfW * 2 * WORLD_UNITS_PER_MM, halfH * WORLD_UNITS_PER_MM, 1);
          scratchMatrix.compose(scratchPosition, FLAT, scratchScale);
          if (instance < pickIds.length) {
            pick.setMatrixAt(instance, scratchMatrix);
            instance += 1;
          }
        }
      }
    });

    const existing = geometry.getAttribute('position') as BufferAttribute | undefined;
    if (grew || existing === undefined || existing.array !== buffer.array) {
      geometry.setAttribute('position', new BufferAttribute(buffer.array, 3));
    } else {
      existing.needsUpdate = true;
    }
    geometry.setDrawRange(0, buffer.vertexCount);
    geometry.computeBoundingSphere();

    if (pick !== null) {
      pick.count = instance;
      pick.instanceMatrix.needsUpdate = true;
      pick.computeBoundingSphere();
    }

    // Labels that mounted with this pass have not been through a camera commit
    // yet, so their scale is still 1. Sync now rather than letting them render
    // one frame at world size, which reads as a flash of enormous text.
    scale.sync();
  }, [items, elevationMm, pickIds]);

  if (items.length === 0) return null;

  const inkColor = materials.dimensionLine.color.getStyle();
  const brandColor = materials.dimensionActive.color.getStyle();

  return (
    <group visible={visible} name="room-tag-overlay">
      <lineSegments
        geometry={geometryRef.current ?? undefined}
        material={materials.leaderLine}
        renderOrder={ROOM_LABEL_RENDER_ORDER}
        frustumCulled={false}
      />

      <instancedMesh
        key={`room-pick-${String(pickIds.length)}`}
        ref={(node) => {
          pickRef.current = node;
          pickRegister(node);
        }}
        args={[pickGeometry, materials.pickProxy, Math.max(1, pickIds.length)]}
        renderOrder={ROOM_LABEL_RENDER_ORDER}
        frustumCulled={false}
      />

      {/* One container; `useScreenScale` walks its children. See the note in
          `render/screenScale.ts` on why per-label registration leaks.

          The `<Suspense>` is load-bearing, not tidiness: `<Text>` suspends on
          the font, and an uncaught suspension is re-thrown by the `<Canvas>`
          into the DOM tree, where the route-level boundary hides the whole
          plan tab — forever, when the font file is missing, because troika's
          load-error path never calls back. Full write-up in
          `DimensionLayer.tsx`; contained here, a broken font costs the label
          TEXT only, while leader lines and click targets stay live. */}
      <group ref={scale.ref}>
        <Suspense fallback={null}>
          {items.map((item, index) => {
            const isHighlighted = highlighted.has(item.tag.roomId);
            const dim = item.placed.kind === 'overflow';
            return (
              <group
                key={item.tag.roomId}
                ref={(node) => {
                  labelRefs.current[index] = node;
                }}
              >
                <Text
                  font={fontUrl}
                  fontSize={LABEL_FONT_SIZE_LOCAL * style.nameFontPx}
                  position={[0, (style.areaFontPx + style.lineGapPx) / 2, 0]}
                  anchorX="center"
                  anchorY="middle"
                  color={isHighlighted ? brandColor : inkColor}
                  fillOpacity={dim ? 0.55 : 1}
                  renderOrder={ROOM_LABEL_RENDER_ORDER + 1}
                  material-depthTest={false}
                  material-depthWrite={false}
                >
                  {item.tag.nameText}
                </Text>
                <Text
                  font={fontUrl}
                  fontSize={LABEL_FONT_SIZE_LOCAL * style.areaFontPx}
                  position={[0, -(style.nameFontPx + style.lineGapPx) / 2, 0]}
                  anchorX="center"
                  anchorY="middle"
                  color={inkColor}
                  fillOpacity={dim ? 0.45 : 0.75}
                  renderOrder={ROOM_LABEL_RENDER_ORDER + 1}
                  material-depthTest={false}
                  material-depthWrite={false}
                >
                  {item.tag.targetText === null
                    ? item.tag.areaText
                    : `${item.tag.areaText} · target ${item.tag.targetText}`}
                </Text>
              </group>
            );
          })}
        </Suspense>
      </group>
    </group>
  );
}
