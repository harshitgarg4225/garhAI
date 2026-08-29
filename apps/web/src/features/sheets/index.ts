/**
 * The Sheets feature (§7, F7-A, D13) — the municipal drawing set.
 *
 * `SheetsTab` is the whole screen; the rest are its parts, exported because the e2e
 * suite and the unit tests address them directly and because `SheetViewer` is reused
 * by anything that needs to show one drawing (the share viewer will).
 */

export { SheetsTab, default as default } from './SheetsTab';
export { SheetViewer, assertRenderableSvg } from './SheetViewer';
export { SheetThumbnail } from './SheetThumbnail';
export { ReviewTray } from './ReviewTray';
export { SubmissionPanel } from './SubmissionPanel';
export { TitleBlockEditor, nextRevisionLetter } from './TitleBlockEditor';
export {
  EXPORT_OPTIONS,
  SHEET_KIND_INFO,
  SHEET_KIND_LABELS,
  annotationText,
  deleteAnnotationOp,
  elementLabel,
  fetchDrawingPreferences,
  fetchProjectSubmission,
  fetchReviewTray,
  fetchSheetContent,
  fetchSheetSet,
  fetchSheetSummary,
  fetchSubmissionReadiness,
  reattachAnnotationOp,
  saveDrawingPreferences,
  saveProjectSubmission,
  sheetDownloadLink,
} from './api';
export type {
  DrawingPreferences,
  ExportKind,
  ReviewTray as ReviewTrayData,
  RevisionRow,
  Sheet,
  SheetAnnotation,
  SheetContent,
  SheetSet,
  ProjectSubmission,
  SheetSetSummary,
  SubmissionReadiness,
  SubmissionTemplate,
  TitleBlock,
} from './api';
