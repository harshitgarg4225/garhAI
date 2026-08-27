export { CommentsPanel } from './CommentsPanel';
export type { CommentsPanelProps } from './CommentsPanel';
export { useComments } from './useComments';
export type { CommentsState } from './useComments';

// Canvas-pinned comments. The panel and the plan's pin layer are two halves of
// one feature that cannot share React context (the `<Canvas>` boundary), so the
// vocabulary they agree on is exported here rather than hidden in the panel.
export {
  ANCHOR_KINDS,
  numberPlanPins,
  pinExcerpt,
  planAnchorPayload,
  planPins,
  readPlanAnchor,
} from './anchor';
export type { AnchorKind, CommentPin, PinFilter, PlanAnchor } from './anchor';
export {
  IDLE_PLACEMENT,
  placementAnchor,
  reducePinPlacement,
  selectPinComments,
  selectPinPlacement,
  useCommentPinStore,
} from './pinStore';
export type { CommentPinState, PinPlacement, PinPlacementEvent } from './pinStore';
