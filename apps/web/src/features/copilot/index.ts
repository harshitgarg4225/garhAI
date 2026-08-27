/**
 * features/copilot — the natural-language editing rail (Phase 6, §10).
 *
 * What the integrator wires:
 *
 *   1. Mount `<CopilotPanel />` on the project shell's right side. It renders
 *      nothing while the ui store's `copilotOpen` is false, so mounting is
 *      unconditional.
 *   2. Bind the `/` shortcut: add a `copilot.focus` command to
 *      `lib/keymap.ts` KEY_BINDINGS with `key: COPILOT_FOCUS_KEY`, and pass
 *      `{'copilot.focus': copilotFocusHandler}` where the app-wide shortcuts
 *      are registered. The handler opens the rail and focuses the input.
 *
 * Everything a copilot answer changes goes through `useModelStore.dispatch`
 * as one op group — the same path as a hand edit. There is no other write.
 */

export { CopilotPanel } from './CopilotPanel';
export type { CopilotPanelProps } from './CopilotPanel';

export {
  COPILOT_FOCUS_KEY,
  copilotFocusHandler,
  focusCopilotInput,
  registerCopilotInput,
} from './focus';

export { useCopilotStore, selectTurns, selectBusy, selectHistory, toModelOps } from './useCopilot';
export type { CopilotState } from './useCopilot';

export { proposeCopilot } from './api';
export type { ProposeInput } from './api';

export { toDiffOps, describeOp, opKind, opElementIds, clarificationChips } from './plain';
export { docPlanForStorey, docPlanViewBox, pickDiffStoreyId } from './docPlan';
export { MiniDocPlan } from './MiniDocPlan';

export type { CopilotProposal, CopilotTurn, CopilotTurnStatus, CopilotWireOp } from './types';
