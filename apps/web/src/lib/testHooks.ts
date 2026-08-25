/**
 * testHooks.ts — the dev-build handle the Playwright specs are allowed.
 *
 * ## Why this exists, and its exact boundary
 *
 * A WebGL canvas has no accessible structure, so the e2e suite asserts against
 * the SERVER (the op log and its fold) and drives the UI with real pointer
 * events wherever the target's position can be derived (plan-canvas.spec.ts
 * calibrates the 2D camera and clicks in millimetres). Two Phase-5 targets
 * cannot be positioned that way honestly:
 *
 *   · a specific FACADE COMPONENT mesh in a perspective view — its pixel
 *     position depends on orbit state the test would have to reimplement the
 *     camera maths to predict;
 *   · and nothing else. Everything the spec can reach through pixels or the
 *     DOM, it must.
 *
 * So this module exposes exactly one *arrangement* affordance — programmatic
 * selection, the same store write a real pick lands in — plus read-only
 * probes. It can APPLY no op, DISPATCH nothing, and MUTATE nothing but the
 * selection; a spec that used it to skip real interaction would still have to
 * produce its ops through the UI.
 *
 * ## Why it is safe
 *
 * Installed only when `import.meta.env.DEV` is true — Vite statically replaces
 * that with `false` in production builds and the whole call tree is
 * tree-shaken out. Nothing here reads secrets, and the e2e stack runs the Vite
 * dev server (`docker compose up`, per DECISIONS.md), so the hook exists
 * exactly where the specs run.
 */

import { useCopilotStore } from '../features/copilot/useCopilot';
import { useModelStore } from '../stores/model';
import { useSelectionStore } from '../stores/selection';
import { useThreeStore } from '../stores/three';
import { useUiStore } from '../stores/ui';

/**
 * PHASE 6/7 ADDITIONS — and why they are still read-only.
 *
 * The copilot spec drives everything through the real UI: it types into the
 * real command box, reads the real diff, and clicks the real Apply button. What
 * pixels cannot answer is *which op group the store minted*, and asserting
 * "exactly one undo group" from the DOM would mean counting toast text. So the
 * snapshot gained `copilotTurns` / `copilotLastGroupId` / `undoDepth` — three
 * numbers the spec correlates with the SERVER's op log, which stays the source
 * of truth for what was actually written.
 *
 * Still no write affordance: nothing here can send a command, apply a proposal,
 * or start a render. A spec that could would be testing this file.
 */
export interface GarhTestHooks {
  /** Replace the selection — the same store write a canvas pick performs. */
  readonly select: (ids: readonly string[]) => void;
  /** Read-only state probe for expect.poll. */
  readonly snapshot: () => {
    readonly selectedIds: readonly string[];
    readonly viewMode: '2d' | '3d';
    readonly headIdx: number;
    readonly pendingCount: number;
    readonly visibleStoreyId: string | null;
    readonly facadeKitId: string | null;
    readonly facadeSeed: number;
    readonly facadeComponentCount: number;
    readonly rebuildCount: number;
    readonly lastRebuildMs: number | null;
    readonly engineStatus: string;
    /** Conversation length — how many commands this session has sent. */
    readonly copilotTurns: number;
    /** Status of the newest turn: thinking | ready | applied | cannot | … */
    readonly copilotLastStatus: string | null;
    /** The group id the newest ready/applied turn carries. Server-minted. */
    readonly copilotLastGroupId: string | null;
    /** Ops in the newest proposal — what Apply would dispatch, as one group. */
    readonly copilotLastOpCount: number;
    /** Undo entries. One copilot apply must add exactly one, whatever its size. */
    readonly undoDepth: number;
  };
}

declare global {
  interface Window {
    __garhTestHooks?: GarhTestHooks;
  }
}

/** Idempotent. Called from the editor page's mount effect, DEV builds only. */
export function installTestHooks(): void {
  if (!import.meta.env.DEV) return;
  if (typeof window === 'undefined' || window.__garhTestHooks !== undefined) return;
  window.__garhTestHooks = {
    select: (ids) => {
      useSelectionStore.getState().selectMany(ids);
    },
    snapshot: () => {
      const model = useModelStore.getState();
      const three = useThreeStore.getState();
      const turns = useCopilotStore.getState().turns;
      const lastTurn = turns[turns.length - 1];
      return {
        selectedIds: useSelectionStore.getState().ids,
        viewMode: useUiStore.getState().viewMode,
        headIdx: model.headIdx,
        pendingCount: model.pending.length,
        visibleStoreyId: three.visibleStoreyId,
        facadeKitId: three.appliedFacade.kitId,
        facadeSeed: three.appliedFacade.seed,
        facadeComponentCount: three.appliedFacade.componentCount,
        rebuildCount: three.lastRebuild?.rebuildCount ?? 0,
        lastRebuildMs: three.lastRebuild?.ms ?? null,
        engineStatus: three.engineStatus,
        copilotTurns: turns.length,
        copilotLastStatus: lastTurn?.status ?? null,
        copilotLastGroupId: lastTurn?.groupId ?? null,
        // `ops` is cleared on reject, so read the proposal: what was OFFERED is
        // what "one group of N" is asserted against.
        copilotLastOpCount: lastTurn?.proposal?.ops.length ?? 0,
        undoDepth: model.undoStack.length,
      };
    },
  };
}
