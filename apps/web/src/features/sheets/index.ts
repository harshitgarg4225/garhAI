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
export { TitleBlockEditor, nextRevisionLetter } from './TitleBlockEditor';
export {
  EXPORT_OPTIONS,
  SHEET_KIND_INFO,
  SHEET_KIND_LABELS,
  annotationText,
  deleteAnnotationOp,
  elementLabel,
  fetchDrawingPreferences,
  fetchReviewTray,
  fetchSheetContent,
  fetchSheetSet,
  fetchSheetSummary,
  reattachAnnotationOp,
  saveDrawingPreferences,
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
  SheetSetSummary,
  TitleBlock,
} from './api';
