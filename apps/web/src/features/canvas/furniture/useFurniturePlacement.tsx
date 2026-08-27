/**
 * The furniture feature's one binding to the stores.
 *
 * Components in this folder use THIS hook, never `useModelStore` directly, for
 * the same reason `features/plot/usePlot.ts` exists: one file knows how the
 * feature reads and writes, and the op path is visibly the only write path.
 *
 * ## Why a provider
 *
 * The browser panel, the canvas layer and the HUD must all drive the SAME tool
 * state machine. A plain hook would give each of them its own controller and
 * three previews that disagree. The provider owns exactly one
 * {@link PlacementController} and hands it out.
 *
 * INTEGRATOR: wrap the plan surface once —
 *
 * ```tsx
 * <FurniturePlacementProvider>
 *   <CanvasRoot />          // renders <FurnitureLayer/> inside its <Canvas>
 *   <FurnitureBrowser />    // side rail
 *   <FurniturePlacementHud />
 * </FurniturePlacementProvider>
 * ```
 *
 * ## Render discipline (§14)
 *
 * This provider re-renders when the DOCUMENT changes (an op landed), when the
 * storey or snap mode changes, or when the tool arms/disarms. It does NOT
 * re-render on pointer movement: `onPointerMoveMm` is a stable callback that
 * writes into the controller, and the controller publishes moves on its
 * imperative channel. Confirm it if you touch this file — a `useState` added
 * here for a pose is the single easiest way to blow the frame budget.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
  type ReactNode,
} from 'react';

import type { Pt, RoomType, UnitsDisplay } from '@garh/model';

import { useModelStore } from '../../../stores/model';
import { useSelectionStore } from '../../../stores/selection';
import { selectActiveStoreyId, selectSnapStepMm, useUiStore } from '../../../stores/ui';
import { buildPlacementContext } from './collision';
import { roomAtPt, snapPtMm } from './geometry';
import {
  PlacementController,
  suggestRotationDeg,
  type CommitResult,
  type FurnitureKeyEvent,
  type KeyOutcome,
  type PlacementPhase,
  type PointerModifiers,
} from './placement';
import type { CatalogueItem, PlacedFurniture, Pose, RoomLike } from './types';
import { useFurnitureCatalogue, type FurnitureCatalogue } from './useFurnitureCatalogue';

export interface FurniturePlacementValue {
  readonly controller: PlacementController;
  readonly catalogue: FurnitureCatalogue;
  /** Instances on the active storey, joined to their catalogue entries. */
  readonly placed: readonly PlacedFurniture[];
  readonly unitsDisplay: UnitsDisplay;
  /** Room type of the current selection, for the browser's default filter. */
  readonly selectedRoomType: RoomType | null;
  readonly activeStoreyId: string | null;

  // ── coarse tool state (safe to render) ───────────────────────────────────
  readonly phase: PlacementPhase;
  readonly armedItem: CatalogueItem | null;

  // ── commands ─────────────────────────────────────────────────────────────
  /** Arm the tool with an item and switch the canvas to the furniture tool. */
  readonly arm: (item: CatalogueItem, atMm?: Pt) => void;
  readonly cancel: () => void;
  /** Plot-local mm from the canvas core's own screen→plan projection. */
  readonly pointerMove: (ptMm: Pt, mods?: PointerModifiers) => void;
  /** A click on the canvas: commits a placement, or a move that was in flight. */
  readonly pointerDown: (ptMm: Pt) => CommitResult | null;
  /** A drop from the browser panel's drag. */
  readonly dropAt: (catalogId: string, ptMm: Pt) => CommitResult | null;
  /** Start dragging an existing instance (the canvas picked it). */
  readonly beginMove: (furnitureId: string) => boolean;
  readonly deleteSelected: () => CommitResult | null;
  readonly handleKey: (event: FurnitureKeyEvent) => KeyOutcome;
}

const FurniturePlacementContext = createContext<FurniturePlacementValue | null>(null);

export function FurniturePlacementProvider({ children }: { children: ReactNode }): JSX.Element {
  const value = useFurniturePlacementValue();
  return (
    <FurniturePlacementContext.Provider value={value}>
      {children}
    </FurniturePlacementContext.Provider>
  );
}

/**
 * Throws when used outside the provider — deliberately. A silent null would
 * turn "you forgot the provider" into "the furniture tool quietly does
 * nothing", which is a far more expensive bug to find.
 */
export function useFurniturePlacement(): FurniturePlacementValue {
  const value = useContext(FurniturePlacementContext);
  if (value === null) {
    throw new Error(
      'useFurniturePlacement must be used inside <FurniturePlacementProvider>. ' +
        'Wrap the plan surface once, above both the canvas and the furniture browser.',
    );
  }
  return value;
}

// ---------------------------------------------------------------------------
// The implementation
// ---------------------------------------------------------------------------

function useFurniturePlacementValue(): FurniturePlacementValue {
  const controllerRef = useRef<PlacementController | null>(null);
  if (controllerRef.current === null) controllerRef.current = new PlacementController();
  const controller = controllerRef.current;

  const catalogue = useFurnitureCatalogue();
  const dispatch = useModelStore((s) => s.dispatch);
  const house = useModelStore((s) => s.doc.house);
  const unitsDisplay = useModelStore((s) => s.doc.house.meta.unitsDisplay);
  const activeStoreyId = useUiStore(selectActiveStoreyId);
  const snapStepMm = useUiStore(selectSnapStepMm);
  const setTool = useUiStore((s) => s.setTool);
  const pushToast = useUiStore((s) => s.pushToast);
  const selectedIds = useSelectionStore((s) => s.ids);
  const select = useSelectionStore((s) => s.select);

  const coarse = useSyncExternalStore(controller.subscribe, controller.getCoarseState);

  // ── derived model views, rebuilt only when the document changes ──────────

  const rooms = useMemo<RoomLike[]>(
    () =>
      house.rooms
        .filter((room) => room.storeyId === activeStoreyId)
        .map((room) => ({ id: room.id, type: room.type, name: room.name, polygon: room.polygon })),
    [house.rooms, activeStoreyId],
  );

  const placed = useMemo<PlacedFurniture[]>(
    () =>
      house.furniture
        .filter((f) => f.storeyId === activeStoreyId)
        .map((f) => ({
          id: f.id,
          storeyId: f.storeyId,
          catalogId: f.catalogId,
          pose: { pt: f.pt, rotationDeg: f.rotationDeg },
          item: catalogue.index.get(f.catalogId) ?? null,
        })),
    [house.furniture, activeStoreyId, catalogue.index],
  );

  const walls = useMemo(
    () => house.walls.filter((w) => w.storeyId === activeStoreyId),
    [house.walls, activeStoreyId],
  );

  /**
   * Obstacles are rebuilt here and NOWHERE else. The dependency list is the
   * honest answer to "what can change a collision result": the geometry, the
   * storey, the snap grid, and which item is exempt because it is being
   * dragged. Pointer position is deliberately absent.
   */
  useEffect(() => {
    controller.setContext(
      buildPlacementContext({
        storeyId: activeStoreyId,
        snapStepMm,
        walls,
        furniture: placed,
        rooms,
        excludeFurnitureId: coarse.instanceId,
      }),
    );
  }, [controller, activeStoreyId, snapStepMm, walls, placed, rooms, coarse.instanceId]);

  // ── selection → the browser's default room filter ────────────────────────

  const selectedRoomType = useMemo<RoomType | null>(() => {
    for (const id of selectedIds) {
      const room = house.rooms.find((r) => r.id === id);
      if (room !== undefined) return room.type;
    }
    return null;
  }, [selectedIds, house.rooms]);

  // ── writing: every path below ends in exactly one dispatch ───────────────

  const apply = useCallback(
    (result: CommitResult | null): CommitResult | null => {
      if (result === null) return null;
      const outcome = dispatch(result.ops, { label: result.label });
      if (!outcome.ok) {
        const first = outcome.issues[0];
        pushToast({
          tone: 'error',
          title: 'That furniture edit did not stick',
          description: first?.message ?? 'The change was rejected before it was saved.',
          dedupeKey: 'furniture-rejected',
        });
        return null;
      }
      return result;
    },
    [dispatch, pushToast],
  );

  const arm = useCallback(
    (item: CatalogueItem, atMm?: Pt) => {
      setTool('furniture');
      const ctx = controller.getContext();
      const at = atMm === undefined ? undefined : snapPtMm(atMm, ctx.snapStepMm);
      const room = at === undefined ? null : roomAtPt(ctx.rooms, at);
      controller.arm(
        item,
        at,
        at === undefined ? 0 : suggestRotationDeg(at, room?.polygon ?? null),
      );
    },
    [controller, setTool],
  );

  const cancel = useCallback(() => {
    controller.cancel();
  }, [controller]);

  const pointerMove = useCallback(
    (ptMm: Pt, mods?: PointerModifiers) => {
      controller.pointerMove(ptMm, mods ?? {});
    },
    [controller],
  );

  const pointerDown = useCallback(
    (ptMm: Pt): CommitResult | null => {
      controller.pointerMove(ptMm, {});
      const result = apply(controller.commit());
      if (result !== null) select(result.furnitureId);
      return result;
    },
    [controller, apply, select],
  );

  /**
   * Drop from the browser. Arms and commits in one gesture, so a drag-and-drop
   * costs one op and one undo step — dragging a sofa in and pressing Cmd-Z must
   * remove the sofa, not leave a half-armed tool behind.
   */
  const dropAt = useCallback(
    (catalogId: string, ptMm: Pt): CommitResult | null => {
      const item = catalogue.index.get(catalogId);
      if (item === undefined) return null;
      const ctx = controller.getContext();
      const at = snapPtMm(ptMm, ctx.snapStepMm);
      const room = roomAtPt(ctx.rooms, at);
      controller.arm(item, at, suggestRotationDeg(at, room?.polygon ?? null));
      const result = apply(controller.commit());
      if (result !== null) select(result.furnitureId);
      controller.cancel();
      return result;
    },
    [controller, catalogue.index, apply, select],
  );

  const beginMove = useCallback(
    (furnitureId: string): boolean => {
      const target = placed.find((p) => p.id === furnitureId);
      if (target?.item == null) return false;
      setTool('furniture');
      const pose: Pose = target.pose;
      controller.beginMove(target.id, target.item, pose);
      return true;
    },
    [controller, placed, setTool],
  );

  /**
   * Delete every selected furniture instance as ONE undo step, with the §15
   * undo toast. Ignores selected walls, rooms and openings — those belong to
   * their own tools.
   */
  const deleteSelected = useCallback((): CommitResult | null => {
    const targets = placed.filter((p) => selectedIds.includes(p.id));
    if (targets.length === 0) return null;
    const result = controller.deleteOps(
      targets.map((t) => t.id),
      targets.map((t) => t.item),
    );
    const applied = apply(result);
    if (applied !== null) {
      pushToast({
        tone: 'info',
        title: applied.label,
        action: {
          label: 'Undo',
          run: () => {
            useModelStore.getState().undo();
          },
        },
      });
    }
    return applied;
  }, [controller, placed, selectedIds, apply, pushToast]);

  const handleKey = useCallback(
    (event: FurnitureKeyEvent): KeyOutcome => {
      const outcome = controller.handleKey(event);
      if (outcome.commit !== undefined && outcome.commit !== null) {
        const applied = apply(outcome.commit);
        if (applied !== null) select(applied.furnitureId);
      }
      return outcome;
    },
    [controller, apply, select],
  );

  return useMemo(
    () => ({
      controller,
      catalogue,
      placed,
      unitsDisplay,
      selectedRoomType,
      activeStoreyId,
      phase: coarse.phase,
      armedItem: coarse.item,
      arm,
      cancel,
      pointerMove,
      pointerDown,
      dropAt,
      beginMove,
      deleteSelected,
      handleKey,
    }),
    [
      controller,
      catalogue,
      placed,
      unitsDisplay,
      selectedRoomType,
      activeStoreyId,
      coarse.phase,
      coarse.item,
      arm,
      cancel,
      pointerMove,
      pointerDown,
      dropAt,
      beginMove,
      deleteSelected,
      handleKey,
    ],
  );
}
