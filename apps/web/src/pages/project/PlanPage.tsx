/**
 * PlanPage — THE editor surface: the 2D plan (§F4, Phase 4) and, since
 * Phase 5, the 3D view — one page, both tabs, both camera modes.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THIS FILE IS
 * ════════════════════════════════════════════════════════════════════════════
 * Composition, and almost nothing else. The Phase-4 canvas arrived as four
 * independent modules with sharp contracts, and this page is where they meet:
 *
 *   features/canvas/core       one `<Canvas>`, one camera rig, ONE picker
 *   features/canvas/tools      the eight state machines + the keyboard map
 *   features/canvas/overlays   dimensions, room tags, compliance, inspector
 *   features/canvas/furniture  the catalogue, placement, the box proxies
 *   pages/project/plan/*       the plan itself — walls, openings, rooms,
 *                              stairs, balconies, columns, the tool preview
 *                              (nobody owned this; see `planGeometry.ts`)
 *   pages/project/three/*      Phase 5: the extruded building, the facade
 *                              layer, the sun light, the 3D controls
 *
 * The §12 frame around it — tool rail, inspector, compliance strip, storey
 * tabs, undo/redo — belongs to `ProjectShell`, which is why this file renders
 * a bare surface and not a layout.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * PHASE 5 — ONE PAGE FOR TWO TABS, AND WHY
 * ════════════════════════════════════════════════════════════════════════════
 * `routes.tsx` mounts THIS component for both the Plan and the 3D tab (same
 * lazy component reference, so React reconciles the tab switch in place). The
 * §12 Tab binding must swap the camera rig and the layer set "in place — same
 * scene, same selection, same picker", and a second page component would have
 * remounted `<CanvasRoot>`: a new scene graph, a new PickRegistry and a fresh
 * WASM warm-up on every Tab press. Instead:
 *
 *   · `ui.viewMode` stays the single truth the Tab key writes (it pre-dates
 *     Phase 5); this page mirrors it to the URL (`/plan` ↔ `/3d`, replace, so
 *     Tab does not grow the history) and the URL back to it on tab clicks.
 *   · `mode={viewMode}` swaps the `CameraRig` projection over the SAME scene.
 *   · The layer set swaps with it: PlanScene/overlays in 2D, `ThreeDLayers`
 *     (building + facade + sun) in 3D. Selection lives in the selection store
 *     and survives untouched — what you picked in 2D is ringed in 3D and vice
 *     versa, through the one picker.
 *   · The tool controller is 2D-only (`enabled: is2d`): the eight drawing
 *     tools are plan state machines, and 3D's W/A/S/D belongs to walking
 *     (useNav3d's stated integration contract). While it is enabled it also
 *     owns the overlapping keyboard commands — `ui.toolKeysActive` is how the
 *     app-wide map knows to stand down (see `lib/shortcuts.ts`).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHO OWNS THE KEYBOARD
 * ════════════════════════════════════════════════════════════════════════════
 * `useToolController` installs the §12 map (V/W/D/N/S/B/M/F, ⌘Z/⌘Y, 1/2/3,
 * Tab, G, Esc, Enter, Delete) plus the capture-phase layer that lets a tool
 * claim digits mid-draw. This page must NOT re-register any of those.
 *
 * It registers only the Phase-4 additions the tool layer has no opinion about —
 * fit, zoom, the layer toggles, select-all, and the shortcuts sheet. Both
 * listeners sit on `document` in the bubble phase with disjoint command sets,
 * so an event is handled exactly once. `edit.delete` is deliberately NOT here:
 * `BaseTool.wantsKey` claims Delete for every tool, and a second owner would
 * be a second delete.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE FRAME BUDGET (§14: <16 ms during pan/zoom)
 * ════════════════════════════════════════════════════════════════════════════
 *  · `frameloop="demand"` — an idle canvas renders zero frames.
 *  · Camera state lives in `ViewportController`, outside React. A pan is a
 *    vector write and one draw; this component does not re-render.
 *  · The zoom readout is mirrored into the `ui` store only when the printed
 *    SCALE changes (1:100 → 1:150), not per frame. That is the one place this
 *    page could have blown the budget and the one place it is guarded.
 *  · Pointer moves reach the tools through `canvasHandlers`, which reads the
 *    stores with `getState()` rather than hooks. No render per move.
 *  · Geometry is memoised on document identity; the model store replaces the
 *    document exactly once per op group.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ADAPTED CONTRACTS (all four modules, honestly noted)
 * ════════════════════════════════════════════════════════════════════════════
 *  1. `<FurnitureLayer>` defaults to `axes="z-up"`, `sceneUnitsPerMm={1}`. The
 *     core's rig is Y-up metres (`worldZ = −mmY × 0.001`), so it is mounted
 *     with `axes="y-up" sceneUnitsPerMm={0.001}` — the two values the furniture
 *     module's integrator note asks to be checked.
 *  2. Furniture has TWO placement paths: `FurnitureTool` (tool layer) and
 *     `PlacementController` (furniture layer). Canvas clicks go to the tool
 *     controller ONLY, so a click can never commit twice. The furniture
 *     provider keeps the browser, the drag-and-drop drop target, and the layer
 *     that draws placed items; `armedItem` is bridged into the tool settings so
 *     picking an item in the browser is what the F tool then places.
 *  3. `ComplianceChipStrip` (overlays) is not mounted. `ProjectShell` already
 *     owns the §12 bottom strip for every tab, and two strips is one too many.
 *     The overlays' `useComplianceOverlay` still drives the on-canvas markers
 *     and the focus boxes, and the shell's strip reaches the camera through
 *     `ui.requestCanvasFocus`.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ptRound } from '@garh/model';
import { Button, Icon, cn } from '@garh/ui';

import {
  CanvasRoot,
  dollyOrbit,
  Grid,
  OutlinePolyline,
  scaleLabel,
  watchCanvasTheme,
  zoomAtCentre,
  type CanvasCore,
  type CanvasPointerEvent,
  type PickHit,
} from '../../features/canvas/core';
import { FacadeKitPanel } from '../../features/canvas/facade';
import { swatchHex, useMaterialsCatalogue } from '../../features/canvas/materials';
import {
  buildingExtentOf,
  NavModeHud,
  SunPanel,
  useNav3d,
} from '../../features/canvas/sun';
import {
  ComplianceMarkerLayer,
  DimensionEditor,
  DimensionLayer,
  RoomTagLayer,
  buildDimensionChains,
  disposeOverlayMaterials,
  parseRoomTagHandle,
  refreshOverlayMaterials,
  roomTags,
  useComplianceOverlay,
  useDimensionEditing,
  type DimensionHandleIndex,
} from '../../features/canvas/overlays';
import {
  ToolHud,
  ToolOptionsBar,
  useToolController,
  useToolSettings,
} from '../../features/canvas/tools';
import {
  FurnitureBrowser,
  FurnitureLayer,
  FurniturePlacementHud,
  FurniturePlacementProvider,
  isFurnitureDrag,
  readFurnitureDragPayload,
  useFurniturePlacement,
} from '../../features/canvas/furniture';
import { RenderCaptureBridge, RenderLauncher } from '../../features/renders';
import { ShortcutsDialog } from '../../components';
import { useKeyboardMap, type CommandHandlers } from '../../lib/keymap';
import { installTestHooks } from '../../lib/testHooks';
import { useModelStore } from '../../stores/model';
import { useSelectionStore } from '../../stores/selection';
import {
  selectActiveStoreyId,
  selectCanvasLayers,
  selectKeyboardEnabled,
  selectSnapMode,
  selectSnapStepMm,
  selectViewMode,
  useUiStore,
} from '../../stores/ui';
import { useProjectOutlet } from '../ProjectShell';
import { StoreyVisibilityBar, ThreeDLayers, ThreeDStatusChip } from './three';
import {
  PlanScene,
  PreviewLayer,
  RoomTagEditor,
  SelectionLayer,
  disposePlanMaterials,
  planExtentMm,
  elementsExtentMm,
  refreshPlanMaterials,
  storeyFflMm,
  useFurnitureItems,
  useSetbackContext,
  type RoomTagEditSession,
} from './plan';

/** Keyboard zoom step. One press ≈ a third of a wheel notch's feel. */
const KEY_ZOOM_FACTOR = 1.25;

export function PlanPage(): JSX.Element {
  return (
    // One provider above the canvas AND the browser panel, per the furniture
    // module's integrator contract. R3F ≥8.8 bridges React context into the
    // renderer's own reconciler, so the layer inside `<Canvas>` sees it.
    <FurniturePlacementProvider>
      <PlanEditor />
    </FurniturePlacementProvider>
  );
}

function PlanEditor(): JSX.Element {
  const navigate = useNavigate();
  const { tab } = useParams<{ tab: string }>();
  const { project, units, compliance, complianceChecking } = useProjectOutlet();

  // ── model ────────────────────────────────────────────────────────────────
  const house = useModelStore((s) => s.doc.house);
  const plotBoundary = useModelStore((s) => s.doc.plot.boundary);
  const modelReady = useModelStore((s) => s.status === 'ready');

  // ── the tab ↔ view-mode contract (Phase 5, see the header) ──────────────
  // The URL is followed BEFORE the first paint (a useState initialiser runs
  // once, during the initial render), so landing directly on /3d never flashes
  // a frame of the plan. `setViewMode` no-ops when already equal.
  const tabMode: '2d' | '3d' = tab === '3d' ? '3d' : '2d';
  useState(() => {
    useUiStore.getState().setViewMode(tabMode);
  });
  // Tab clicks after mount: URL → store.
  useEffect(() => {
    useUiStore.getState().setViewMode(tabMode);
  }, [tabMode]);

  // ── chrome ───────────────────────────────────────────────────────────────
  const activeStoreyId = useUiStore(selectActiveStoreyId);
  const viewMode = useUiStore(selectViewMode);
  const snapMode = useUiStore(selectSnapMode);
  const snapStepMm = useUiStore(selectSnapStepMm);
  const layers = useUiStore(selectCanvasLayers);
  const keyboardEnabled = useUiStore(selectKeyboardEnabled);
  const activeTool = useUiStore((s) => s.activeTool);
  const canvasFocus = useUiStore((s) => s.canvasFocus);

  const is2d = viewMode === '2d';
  // Pointer handlers read the mode through a ref: their identity must not
  // change per mode or `useCanvasControls` re-binds listeners on every Tab.
  const is2dRef = useRef(is2d);
  is2dRef.current = is2d;

  // The Tab KEY writes the store; mirror it back to the URL so the shell's tab
  // strip follows. `replace` — toggling views must not grow the history.
  useEffect(() => {
    if (viewMode === tabMode) return;
    navigate(`/projects/${project.id}/${viewMode === '3d' ? '3d' : 'plan'}`, { replace: true });
  }, [viewMode, tabMode, navigate, project.id]);

  // The tool controller owns the overlapping keyboard commands only while it
  // is enabled; the app-wide map (`lib/shortcuts.ts`) stands down on this flag
  // and takes back over in 3D and on unmount.
  useEffect(() => {
    useUiStore.getState().setToolKeysActive(is2d);
    return () => {
      useUiStore.getState().setToolKeysActive(false);
    };
  }, [is2d]);

  // Dev-build Playwright handle (no-op and tree-shaken in production).
  useEffect(() => {
    installTestHooks();
  }, []);

  const selectedIds = useSelectionStore((s) => s.ids);
  const hoverId = useSelectionStore((s) => s.hoverId);

  const [core, setCore] = useState<CanvasCore | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // `useNav3d` needs the element as STATE (a ref never re-renders the hook);
  // the callback keeps the existing ref for the drag-and-drop rect maths.
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null);
  const attachContainer = useCallback((el: HTMLDivElement | null) => {
    containerRef.current = el;
    setContainerEl(el);
  }, []);

  const elevationMm = useMemo(() => storeyFflMm(house, activeStoreyId), [house, activeStoreyId]);

  // ── 3D: material colours for the extruded building (op 29 rendering) ────
  // Module-level promise cache inside the hook: one fetch per session, shared
  // with the MaterialsPanel in the inspector rail.
  const materialsCatalogue = useMaterialsCatalogue();
  const materialColors = useMemo<Readonly<Record<string, string>> | undefined>(() => {
    if (materialsCatalogue.loadable.state !== 'ready') return undefined;
    const out: Record<string, string> = {};
    for (const item of materialsCatalogue.loadable.data) out[item.id] = swatchHex(item);
    return out;
  }, [materialsCatalogue.loadable]);

  /**
   * Theming and GPU cleanup for the two material sets this page introduces.
   *
   * `CanvasRoot` already watches the theme for the CORE's materials and calls
   * `disposeCanvasMaterials` when the last canvas unmounts. Nothing did the
   * same for the overlays' set (its own author flagged the gap) or for the
   * plan's, so it is hooked here — the one place that mounts both.
   */
  useEffect(() => {
    const stop = watchCanvasTheme(() => {
      refreshOverlayMaterials();
      refreshPlanMaterials();
    });
    return () => {
      stop();
      disposeOverlayMaterials();
      disposePlanMaterials();
    };
  }, []);

  // ── tools (2D only — see the Phase 5 header note) ────────────────────────
  const setback = useSetbackContext();
  const furnitureItems = useFurnitureItems();
  const tools = useToolController({
    core,
    enabled: is2d,
    setback,
    furnitureCatalog: furnitureItems.itemsById,
  });

  // ── overlays ─────────────────────────────────────────────────────────────
  const chains = useMemo(() => {
    if (activeStoreyId === null) return [];
    const walls = house.walls.filter((w) => w.storeyId === activeStoreyId);
    return buildDimensionChains(walls, house.openings).chains;
  }, [house, activeStoreyId]);

  const tags = useMemo(
    () => (activeStoreyId === null ? [] : roomTags(house.rooms, activeStoreyId, units)),
    [house.rooms, activeStoreyId, units],
  );

  const overlay = useComplianceOverlay({
    issues: compliance,
    checking: complianceChecking,
    house,
    activeStoreyId,
  });

  /**
   * Elements a visible chip points at, drawn in the fail colour.
   *
   * Taken from `overlay.markers`, not from `overlay.chips`: markers are already
   * filtered to the active storey by the overlays layer, and its reason is
   * exactly right — "a violation on the first floor drawn over the ground-floor
   * plan is an assertion that the ground floor is wrong, which it is not".
   */
  const violationIds = useMemo(
    () => overlay.markers.flatMap((marker) => marker.elementIds),
    [overlay.markers],
  );

  const dimensionEditing = useDimensionEditing();
  const handleIndexRef = useRef<DimensionHandleIndex | null>(null);
  const [tagSession, setTagSession] = useState<RoomTagEditSession | null>(null);

  const tagRoom = useMemo(
    () => (tagSession === null ? null : (house.rooms.find((r) => r.id === tagSession.roomId) ?? null)),
    [house.rooms, tagSession],
  );
  // A room deleted (or undone away) while its label is open takes the field
  // with it, rather than leaving an input bound to nothing.
  useEffect(() => {
    if (tagSession !== null && tagRoom === null) setTagSession(null);
  }, [tagSession, tagRoom]);

  // ── camera ───────────────────────────────────────────────────────────────

  // Reads the stores imperatively so its identity depends only on `core` —
  // see the note on `edit.selectAll` below for why that matters. Mode-aware:
  // in 3D "fit" frames the whole BUILDING (all storeys — that is what the
  // perspective camera shows), not the active storey's plan.
  const fitAll = useCallback(() => {
    if (core === null) return;
    const doc = useModelStore.getState().doc;
    if (core.viewport.mode === '3d') {
      const extent = buildingExtentOf(doc.house);
      if (extent === null) return;
      core.viewport.fitBbox(extent.box, { animate: false });
      return;
    }
    const extent = planExtentMm(
      doc.house,
      useUiStore.getState().activeStoreyId,
      doc.plot.boundary,
    );
    if (extent === null) return;
    core.viewport.fitBbox(extent, { animate: true });
  }, [core]);

  // Fit once, when the model first has something to show. `fitted` is a ref so
  // a later edit never yanks the camera out from under a drawing hand.
  const fitted = useRef(false);
  useEffect(() => {
    if (fitted.current || core === null || !modelReady) return;
    const extent = planExtentMm(house, activeStoreyId, plotBoundary);
    if (extent === null) return;
    fitted.current = true;
    core.viewport.fitBbox(extent, { animate: false });
  }, [core, modelReady, house, activeStoreyId, plotBoundary]);

  // The FIRST entry into 3D frames the building; after that the orbit is the
  // user's and re-entering 3D returns exactly where they left it (the
  // viewport controller keeps both cameras alive — its stated design).
  const fitted3d = useRef(false);
  useEffect(() => {
    if (is2d || fitted3d.current || core === null || !modelReady) return;
    const extent = buildingExtentOf(useModelStore.getState().doc.house);
    if (extent === null) return; // empty model: teach-state below, nothing to frame
    fitted3d.current = true;
    core.viewport.fitBbox(extent.box, { animate: false });
  }, [is2d, core, modelReady]);

  // Mirror the zoom into the `ui` store ONLY when the printed scale changes.
  // Subscribing per frame is the single easiest way to lose the §14 budget on
  // this page, so the guard lives in the store action as well as here.
  // In 3D the label is blanked: a perspective view has no single printable
  // scale, and showing "1:87" would be the readout lying (§15).
  useEffect(() => {
    if (core === null) return undefined;
    const setCanvasZoom = useUiStore.getState().setCanvasZoom;
    const publish = (): void => {
      const mmPerPx = core.viewport.mmPerPx;
      setCanvasZoom(mmPerPx, core.viewport.mode === '2d' ? scaleLabel(mmPerPx) : '');
    };
    publish();
    return core.viewport.subscribeAnimationFrame(publish);
  }, [core]);

  // A compliance chip (or anything else) asking for the camera.
  useEffect(() => {
    if (canvasFocus === null || core === null) return;
    const extent = elementsExtentMm(house, canvasFocus.elementIds);
    if (extent !== null) core.viewport.fitBbox(extent, { animate: true });
    useSelectionStore.getState().selectMany(canvasFocus.elementIds);
    useUiStore.getState().clearCanvasFocus();
  }, [canvasFocus, core, house]);

  // ── the rest of the keyboard map ─────────────────────────────────────────
  const commandHandlers = useMemo<CommandHandlers>(
    () => ({
      'view.fit': () => fitAll(),
      // `=`/`-` zoom in whichever projection is live: 2D scales the frustum,
      // 3D dollies the orbit. One keystroke, one meaning, both views.
      'view.zoomIn': () => {
        if (core === null) return false;
        if (core.viewport.mode === '3d') {
          core.viewport.setOrbit(dollyOrbit(core.viewport.orbit, 1 / KEY_ZOOM_FACTOR));
          return undefined;
        }
        core.viewport.setView2d(zoomAtCentre(core.viewport.view2d, 1 / KEY_ZOOM_FACTOR));
        return undefined;
      },
      'view.zoomOut': () => {
        if (core === null) return false;
        if (core.viewport.mode === '3d') {
          core.viewport.setOrbit(dollyOrbit(core.viewport.orbit, KEY_ZOOM_FACTOR));
          return undefined;
        }
        core.viewport.setView2d(zoomAtCentre(core.viewport.view2d, KEY_ZOOM_FACTOR));
        return undefined;
      },
      'view.grid': () => useUiStore.getState().toggleCanvasLayer('grid'),
      'view.dimensions': () => useUiStore.getState().toggleCanvasLayer('dimensions'),
      'edit.selectAll': () => {
        // Read through `getState()`, not through the render closure. Putting
        // `house` in the dependency list would rebuild this object on every op,
        // and `useKeyboardMap` would detach and re-attach its listener with it —
        // hundreds of times in a drawing session, for no reason.
        const storeyId = useUiStore.getState().activeStoreyId;
        if (storeyId === null) return false;
        const model = useModelStore.getState().doc.house;
        const ids = [
          ...model.walls.filter((w) => w.storeyId === storeyId).map((w) => w.id),
          ...model.furniture.filter((f) => f.storeyId === storeyId).map((f) => f.id),
        ];
        if (ids.length === 0) return false;
        useSelectionStore.getState().selectMany(ids);
        return undefined;
      },
      'help.shortcuts': () => setShortcutsOpen(true),
    }),
    [core, fitAll],
  );
  useKeyboardMap(commandHandlers, { enabled: keyboardEnabled });

  // ── pointer routing ──────────────────────────────────────────────────────

  /**
   * §15 "no dead text": a dimension and a room's area are click-to-edit.
   *
   * ONE AMBIGUITY, HANDLED HONESTLY. `RoomTagLayer` registers the room's NAME
   * line with the bare room id — exactly the id `PlanScene` registers for the
   * room's floor. Both are `kind: 'room'` with the same id, so a single click
   * genuinely cannot tell "clicked the label" from "clicked the floor", and
   * guessing would mean a click anywhere in a bedroom sometimes opening a
   * rename field. So: the AREA line (which carries `AREA_HANDLE_SUFFIX`) opens
   * its editor on a single click, and the NAME opens on a double-click of the
   * room — which is the CAD idiom anyway. The inspector edits both as well.
   */
  const handleClick = useCallback(
    (event: CanvasPointerEvent) => {
      const hit = event.hit();

      // 3D: a click IS selection. The tools are 2D state machines and are
      // disabled here, so this page routes the pick to the selection store
      // directly — the same store, the same ids, the same `selectHit`
      // semantics a 2D select-tool click lands in (shift toggles). This is
      // the "vice versa" half of the 2D↔3D selection contract.
      if (!is2dRef.current) {
        if (hit.id === null || hit.kind === null) {
          useSelectionStore.getState().clear();
          return;
        }
        useSelectionStore.getState().selectHit(
          { kind: hit.kind, id: hit.id, storeyId: hit.storeyId, pointMm: hit.pointMm },
          event.shiftKey ? 'toggle' : 'replace',
        );
        return;
      }

      if (hit.id === null) {
        setTagSession(null);
        return;
      }
      if (hit.kind === 'dimension' && handleIndexRef.current !== null) {
        setTagSession(null);
        dimensionEditing.open(handleIndexRef.current, hit.id, event.pixel);
        return;
      }
      if (hit.kind === 'room') {
        const { roomId, part } = parseRoomTagHandle(hit.id);
        if (part === 'area') {
          setTagSession({ roomId, part, atPx: { x: event.pixel.x, y: event.pixel.y } });
          return;
        }
      }
      setTagSession(null);
    },
    [dimensionEditing],
  );

  /**
   * Double-click means "finish" to every tool, so the tool sees it first and
   * always. Only when the select tool is armed and a room was hit does it also
   * open the rename field — a double-click that ends a wall chain must never
   * also pop a text input.
   */
  const handleDoubleClick = useCallback(
    (event: CanvasPointerEvent) => {
      // 3D: double-click belongs to the nav layer (fit), which listens on the
      // container element itself. Opening a rename field over a perspective
      // view would also position DOM text against a projection it ignores.
      if (!is2dRef.current) return;
      tools.canvasHandlers.onDoubleClick?.(event);
      if (useUiStore.getState().activeTool !== 'select') return;
      const hit = event.hit();
      if (hit.kind !== 'room' || hit.id === null) return;
      const { roomId } = parseRoomTagHandle(hit.id);
      setTagSession({ roomId, part: 'name', atPx: { x: event.pixel.x, y: event.pixel.y } });
    },
    [tools.canvasHandlers],
  );

  const handleHover = useCallback((hit: PickHit | null) => {
    useSelectionStore.getState().setHoverHit(
      hit === null || hit.id === null
        ? null
        : { kind: hit.kind, id: hit.id, storeyId: hit.storeyId, pointMm: hit.pointMm },
    );
  }, []);

  // ── furniture: browser → tool settings, and drag-and-drop ────────────────
  const furniture = useFurniturePlacement();
  const armedId = furniture.armedItem?.id ?? null;
  useEffect(() => {
    // The bridge between the two furniture paths (see the header, note 2).
    // Arming in the browser is what the F tool then places.
    if (armedId !== null) useToolSettings.getState().patch({ furnitureCatalogId: armedId });
  }, [armedId]);

  const dropFurniture = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      const catalogId = readFurnitureDragPayload(event.dataTransfer);
      if (catalogId === null || core === null) return;
      const rect = containerRef.current?.getBoundingClientRect();
      if (rect === undefined) return;
      event.preventDefault();
      const ptMm = core.viewport.pixelToMmF({
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
      if (ptMm === null) return;
      // `ptRound` (half away from zero), not `Math.round` (half up). This point
      // feeds a `furniture.set` payload; `core/coords.ts` states the one
      // rounding rule the canvas has and this is the drop path's use of it.
      furniture.dropAt(catalogId, ptRound(ptMm.x, ptMm.y));
    },
    [core, furniture],
  );

  // ── empty state ──────────────────────────────────────────────────────────
  const isEmpty = modelReady && house.walls.length === 0;

  return (
    <div
      ref={attachContainer}
      className="relative h-full w-full"
      /**
       * The canvas-scope marker.
       *
       * `lib/keymap.ts` decides whether Tab, Enter, Delete and ⌘A are live by
       * asking `target.closest('[data-garh-canvas]')`, and `e2e/support/ui.ts`
       * locates the drawing surface with the same attribute — it is the one
       * selector the specs are allowed, because WebGL has no accessible
       * structure to query. `CanvasRoot` does not set it (it is store-agnostic
       * and does not know it is the plan), so the page that mounts it does.
       * An ANCESTOR is enough: `closest` walks up. The VALUE tracks the live
       * view so the specs can wait on the swap ("plan" | "3d"); the keymap
       * only tests presence.
       */
      data-garh-canvas={is2d ? 'plan' : '3d'}
      onDragOver={(event) => {
        // Without a `preventDefault` here the browser refuses the drop and the
        // drag just springs back, silently. Scoped to OUR payload so a file or
        // a text selection dragged over the canvas still behaves normally.
        if (is2d && isFurnitureDrag(event.dataTransfer)) event.preventDefault();
      }}
      onDrop={dropFurniture}
    >
      <CanvasRoot
        mode={viewMode}
        snapModuleMm={snapStepMm}
        /* 3D shows every storey at once, so picks must too: a filtered pick
           would make a storey-2 wall unclickable while plainly visible. The
           storey-visibility toggle already hides (and thereby unpicks) what
           it filters out. */
        activeStoreyId={is2d ? activeStoreyId : null}
        planeElevationMm={elevationMm}
        /* Built-in pan/zoom gestures are 2D's; in 3D `useNav3d` owns the
           pointer (orbit/walk/dolly-to-cursor) and two wheel handlers would
           dolly twice per notch. */
        navigation={is2d}
        ariaLabel={is2d ? `Plan of ${project.name}` : `3D view of ${project.name}`}
        onCoreReady={setCore}
        {...tools.canvasHandlers}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        onHoverChange={handleHover}
        overlay={
          is2d ? (
            <>
              <ToolOptionsBar
                furnitureCatalog={furnitureItems.items}
                unitsDisplay={units}
                className="pointer-events-auto absolute left-3 top-3"
              />
              <ScaleReadout />
              <ToolHud />
              <FurniturePlacementHud className="pointer-events-none absolute right-3 top-3" />

              {dimensionEditing.session === null ? null : (
                <DimensionEditor
                  atPx={dimensionEditing.session.atPx}
                  valueMm={dimensionEditing.session.valueMm}
                  display={units}
                  error={dimensionEditing.error}
                  onCommit={(mm) => dimensionEditing.commit(house, mm)}
                  onCancel={dimensionEditing.cancel}
                />
              )}

              {tagSession === null || tagRoom === null ? null : (
                <RoomTagEditor
                  session={tagSession}
                  room={tagRoom}
                  display={units}
                  onClose={() => setTagSession(null)}
                />
              )}

              {activeTool === 'furniture' ? (
                <FurnitureBrowser className="pointer-events-auto absolute bottom-3 left-3 max-h-[60%] w-72 overflow-hidden rounded-lg border border-line bg-surface shadow-lg" />
              ) : null}

              {isEmpty ? <PlanEmpty onOpenPlot={() => navigate(`/projects/${project.id}/brief`)} /> : null}
            </>
          ) : (
            <>
              {/* ── the 3D chrome (§8/§14/§15 controls, all honest) ────────── */}
              <StoreyVisibilityBar className="absolute left-1/2 top-3 -translate-x-1/2" />
              {core !== null ? <ThreeDNavOverlay core={core} container={containerEl} /> : null}
              <div className="pointer-events-auto absolute left-3 top-3 max-h-[60%] w-80 overflow-y-auto rounded-lg border border-line bg-surface shadow-lg">
                <FacadeKitPanel />
              </div>
              <SunPanel className="pointer-events-auto absolute bottom-3 left-3 w-80" />
              <ThreeDStatusChip className="absolute bottom-3 right-3" />
              {/* Phase 7: photograph the model → render job (features/renders). */}
              <RenderLauncher className="absolute right-3 top-14" />

              {isEmpty ? <ThreeDEmpty /> : null}
            </>
          )
        }
      >
        {/* ── layers shared by both views ──────────────────────────────────
            The plot boundary and the buildable envelope are context in either
            projection; in 3D they lie on the ground (the datum), not at the
            active storey's FFL. */}
        <Grid fine={snapMode === 'fine'} visible={is2d && layers.grid} />

        {/* Phase 7 (§9): publishes the live renderer to features/renders so
            captures reuse THIS canvas — never a second one. Renders nothing. */}
        <RenderCaptureBridge />

        {plotBoundary.length >= 3 ? (
          <OutlinePolyline
            pointsMm={plotBoundary}
            elevationMm={is2d ? elevationMm : 0}
            tone="preview"
            closed
            dashed
            layer="grid"
          />
        ) : null}
        {setback?.envelope != null ? (
          <OutlinePolyline
            pointsMm={setback.envelope}
            elevationMm={is2d ? elevationMm : 0}
            tone="preview"
            closed
            dashed
            layer="grid"
          />
        ) : null}

        {/* ── the 2D layer set ─────────────────────────────────────────────
            Unmounted in 3D rather than hidden: these are storey-scoped
            drafting layers (dimension text, room tags, tool previews) whose
            meshes unregister from the picker on unmount — nothing invisible
            stays clickable. Tab back re-mounts against the same memoised
            geometry inputs. */}
        {is2d ? (
          <>
            {/* The room wash is part of the DRAWING, so it is not tied to the
                room-tag toggle — that toggle is about the text. */}
            <PlanScene house={house} storeyId={activeStoreyId} elevationMm={elevationMm} />

            {/* `axes` and `sceneUnitsPerMm` are the two values the furniture
                module's integrator note asks to be checked against the real
                rig: the core is Y-up in metres (worldZ = −mmY × 0.001), not
                its default Z-up in millimetres. */}
            {layers.furniture ? (
              <FurnitureLayer
                axes="y-up"
                sceneUnitsPerMm={0.001}
                floorLevelMm={elevationMm}
                showAllClearances={activeTool === 'furniture'}
              />
            ) : null}

            <DimensionLayer
              chains={chains}
              elevationMm={elevationMm}
              storeyId={activeStoreyId}
              display={units}
              activeSegmentId={dimensionEditing.session?.handle.id ?? null}
              onHandleIndex={(index) => {
                handleIndexRef.current = index;
              }}
              visible={layers.dimensions}
            />

            <RoomTagLayer
              tags={tags}
              elevationMm={elevationMm}
              storeyId={activeStoreyId}
              highlightIds={selectedIds}
              visible={layers.roomTags}
            />

            <ComplianceMarkerLayer
              markers={overlay.markers}
              elevationMm={elevationMm}
              visible={layers.compliance}
            />

            <SelectionLayer
              house={house}
              elevationMm={elevationMm}
              selectedIds={selectedIds}
              hoverId={hoverId}
              violationIds={layers.compliance ? violationIds : undefined}
            />

            <PreviewLayer elevationMm={elevationMm} />
          </>
        ) : (
          /* ── the 3D layer set (Phase 5) ────────────────────────────────
             The extruded building, the facade kit meshes, the sun light and
             the selection bridge — same scene graph, same PickRegistry. */
          <ThreeDLayers house={house} materialColors={materialColors} />
        )}
      </CanvasRoot>

      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}

/**
 * The 3D navigation layer + its HUD. A separate component so `useNav3d` (which
 * requires a non-null core) mounts exactly while 3D is on screen — mounting is
 * the enable/disable, so its pointer listeners are attached only when the
 * orbit owns the pointer.
 */
function ThreeDNavOverlay({
  core,
  container,
}: {
  core: CanvasCore;
  container: HTMLDivElement | null;
}): JSX.Element {
  const nav = useNav3d(container, { core, enabled: true });
  return <NavModeHud nav={nav} className="absolute right-3 top-3" />;
}

/**
 * §15: the 3D empty state teaches the one next action. It floats over the
 * live canvas (the ground and sun are real behind it), like `PlanEmpty`.
 */
function ThreeDEmpty(): JSX.Element {
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
      <div className="pointer-events-auto max-w-sm rounded-lg border border-line bg-surface/95 p-5 text-center shadow-lg backdrop-blur">
        <span
          className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-brand-soft text-brand-ink"
          aria-hidden="true"
        >
          <Icon name="cube" size={20} />
        </span>
        <h2 className="text-sm font-semibold text-ink">Nothing to build yet</h2>
        <p className="mt-1 text-xs leading-5 text-ink-muted">
          The 3D view is the plan, extruded — there is no separate model to keep in sync. Press{' '}
          <kbd className="rounded border border-line-strong px-1">Tab</kbd> to flip to the plan,
          draw walls with <kbd className="rounded border border-line-strong px-1">W</kbd>, and Tab
          back to walk around them.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small DOM pieces
// ---------------------------------------------------------------------------

/**
 * "1:100" in the corner.
 *
 * Subscribes to `ui.scaleLabel`, which only changes when the printed scale
 * changes — so this re-renders a handful of times during a zoom gesture rather
 * than sixty times a second. That is the whole reason the store holds a label
 * and not a number.
 */
function ScaleReadout(): JSX.Element | null {
  const label = useUiStore((s) => s.scaleLabel);
  if (label === '') return null;
  return (
    <span
      className={cn(
        'pointer-events-none absolute bottom-3 right-3 rounded-md border border-line',
        'bg-surface/90 px-2 py-1 text-2xs text-ink-muted garh-nums backdrop-blur',
      )}
      aria-label="Drawing scale"
    >
      {label}
    </span>
  );
}

/**
 * §15: an empty state teaches, and offers the next real action.
 *
 * Deliberately NOT `@garh/ui`'s `EmptyState` — that component owns a whole
 * screen and requires a demo-project action. This is a floating card over a
 * live, usable canvas: the tools work behind it, and it must not read as a
 * blocking dialog.
 */
function PlanEmpty({ onOpenPlot }: { onOpenPlot: () => void }): JSX.Element {
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
      <div className="pointer-events-auto max-w-sm rounded-lg border border-line bg-surface/95 p-5 text-center shadow-lg backdrop-blur">
        <span
          className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-brand-soft text-brand-ink"
          aria-hidden="true"
        >
          <Icon name="wall" size={20} />
        </span>
        <h2 className="text-sm font-semibold text-ink">Nothing drawn yet</h2>
        <p className="mt-1 text-xs leading-5 text-ink-muted">
          Press <kbd className="rounded border border-line-strong px-1">W</kbd> and click twice to
          draw your first wall — type a length while you drag to set it exactly. Rooms, areas and
          bye-law checks appear on their own as soon as the walls close.
        </p>
        <div className="mt-3 flex justify-center">
          <Button variant="secondary" size="sm" onClick={onOpenPlot}>
            Start from the plot and brief
          </Button>
        </div>
      </div>
    </div>
  );
}

export default PlanPage;
