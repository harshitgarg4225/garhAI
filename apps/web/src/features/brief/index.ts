/**
 * features/brief — the F2 brief-capture surface (playbook Phase 2).
 *
 * Self-contained: everything here reads the model store and dispatches
 * `brief.update` ops through it (golden rule 1). NOTHING here touches routes,
 * pages/ or the components barrel — wiring into `BriefPage` belongs to the
 * integrator. Suggested composition, top to bottom:
 *
 *   <CompletenessMeter onJumpTo={scrollToSection} />
 *   <FreeTextParse projectId={project.id} />
 *   <BriefForm />
 *   <VastuSelector />
 *
 * All four are independent — the meter re-computes from the store on every
 * dispatch, so no coordination props are needed between them.
 */

export { BriefForm } from './BriefForm';
export type { BriefFormProps } from './BriefForm';

export { VastuSelector } from './VastuSelector';
export type { VastuSelectorProps } from './VastuSelector';

export { CompletenessMeter } from './CompletenessMeter';
export type { CompletenessMeterProps } from './CompletenessMeter';

export { FreeTextParse } from './FreeTextParse';
export type { FreeTextParseProps } from './FreeTextParse';

export { useBrief } from './useBrief';
export type { UseBrief, BriefUpdateArgs } from './useBrief';

export {
  computeCompleteness,
  COMPLETENESS_CHECKLIST,
  COMPLETENESS_TOTAL_WEIGHT,
} from './completeness';
export type { CompletenessItem, CompletenessResult } from './completeness';

export {
  applyMergePatch,
  briefUpdateOp,
  canonicaliseParsedData,
  diffMergePatch,
  pruneUnchanged,
  setBriefField,
} from './mergePatch';
export type { BriefUpdateOptions } from './mergePatch';

export {
  BUDGET_BANDS,
  DEFAULT_RATE_PER_SQFT_INR,
  KITCHEN_TYPES,
  KITCHEN_TYPE_LABELS,
  OPTIONAL_ROOM_TYPES,
  STYLE_KITS,
  VASTU_DEFAULT_PREFS,
  VASTU_ZONE_RULES,
  addBedroom,
  areaTargetMm2,
  bandForBudget,
  bedroomRows,
  normaliseRooms,
  otherRooms,
  parseRupees,
  readBriefData,
  removeBedroom,
  roomCount,
  roomTypeLabel,
  setRoomCount,
  updateBedroom,
  withLivingDining,
} from './types';
export type {
  AdjacencyWish,
  BathChoice,
  BriefData,
  BudgetBandId,
  KitchenType,
  LivingDining,
  OptionalRoomType,
  RoomRequest,
  StyleKitId,
  VastuPrefs,
  VastuZoneKey,
} from './types';
