/**
 * api.ts — the Sheets feature's transport (§7, F7-A, D13).
 *
 * The endpoint calls and their zod shapes live in `lib/api.ts::api.sheets` and
 * `lib/schemas.ts` — the catalogued §11 surface. This module is the feature's own
 * vocabulary on top of it: the six sheet kinds with their copy, the export menu, the
 * thin read wrappers each component uses, and the two op builders the review tray
 * dispatches.
 *
 * Two contracts worth stating, because both were bugs before:
 *
 * 1. **`GET /sheets` is not a page.** The server answers `SheetSetOut`
 *    (`{projectId, designVersionId, sheets[], generatedAt}`), not `{items, nextCursor}`.
 *    `lib/api.ts` used to parse it with `pageParser`, so the tab saw an empty list for
 *    a project with a full set. `sheetSetSchema` is the real shape.
 *
 * 2. **Annotations are written as ops, never posted here.** Op 32 (`annotation.set`)
 *    through the model store is the only writer — that is what gives a note undo,
 *    version pinning and provenance. {@link reattachAnnotationOp} and
 *    {@link deleteAnnotationOp} build those ops; the review tray dispatches them.
 */

import { api } from '../../lib/api';

import type { Op } from '@garh/model';

// ---------------------------------------------------------------------------
// Types — all defined in lib/schemas.ts, re-exported here so a component in this
// feature imports one module. The definitions live there because `lib/api.ts` is
// what parses them, and a transport layer must not depend on a feature layer.
// ---------------------------------------------------------------------------

export type {
  DownloadLink,
  DrawingPreferencesResponse as DrawingPreferences,
  ReviewTrayResponse as ReviewTray,
  RevisionRowValue as RevisionRow,
  Sheet,
  SheetAnnotationResponse as SheetAnnotation,
  SheetContentResponse as SheetContent,
  SheetSetResponse as SheetSet,
  SheetSetSummaryResponse as SheetSetSummary,
  TitleBlockValue as TitleBlock,
} from '../../lib/schemas';

import type {
  DownloadLink,
  DrawingPreferencesResponse,
  ReviewTrayResponse,
  SheetContentResponse,
  SheetSetResponse,
  SheetSetSummaryResponse,
} from '../../lib/schemas';

// ---------------------------------------------------------------------------
// Sheet metadata
// ---------------------------------------------------------------------------

/**
 * The six F7-A sheet kinds, in submission order, with the copy the empty state and
 * the thumbnail captions use. `kind` matches the DB vocabulary the API returns.
 */
export const SHEET_KIND_INFO: readonly {
  kind: string;
  label: string;
  detail: string;
}[] = [
  {
    kind: 'site',
    label: 'Site plan',
    detail:
      'Plot, dimensioned setbacks, footprint, road, north arrow, and the coverage and FAR note.',
  },
  {
    kind: 'floor',
    label: 'Floor plan',
    detail:
      'One per storey at 1:100 — overall chains, wall breakpoints, opening centrelines, room dims, areas in sq ft, door/window tags, FFL markers, stair arrow and riser count.',
  },
  {
    kind: 'elevation',
    label: 'Elevation',
    detail: 'All four, with floor lines, level markers and finish callouts from the facade model.',
  },
  {
    kind: 'section',
    label: 'Section',
    detail:
      'Through the staircase: storey heights, sill and lintel levels, plinth, parapet, and the indicative foundation line.',
  },
  {
    kind: 'schedule',
    label: 'Door & window schedule',
    detail: 'Grouped by size into D1.., W1.., V1.. tags, with counts per storey.',
  },
  {
    kind: 'area-statement',
    label: 'Area statement',
    detail:
      'Plot, built-up per floor, carpet, FAR achieved versus allowed, coverage, and the setback table — the same numbers as the compliance checks, from one source.',
  },
];

export const SHEET_KIND_LABELS: Readonly<Record<string, string>> = Object.fromEntries(
  SHEET_KIND_INFO.map((info) => [info.kind, info.label]),
);

export type ExportKind = 'pdf-set' | 'dxf' | 'gltf' | 'png-pack';

/** The download menu, in the order it is offered. */
export const EXPORT_OPTIONS: readonly {
  kind: ExportKind;
  label: string;
  detail: string;
  icon: 'download' | 'sheet' | 'cube' | 'image';
}[] = [
  {
    kind: 'pdf-set',
    label: 'PDF set',
    detail: 'Every sheet, one page each, vector and print-true at 1:100.',
    icon: 'sheet',
  },
  {
    kind: 'dxf',
    label: 'DXF',
    detail: 'One file, one block per sheet, on the A-WALL / A-DOOR / A-DIM layers.',
    icon: 'download',
  },
  {
    kind: 'gltf',
    label: '3D model (glTF)',
    detail: 'For Lumion, D5 or SketchUp.',
    icon: 'cube',
  },
  {
    kind: 'png-pack',
    label: 'Images (PNG)',
    detail: 'A zip of every sheet as an image, for WhatsApp or email.',
    icon: 'image',
  },
];

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export function fetchSheetSet(
  projectId: string,
  version?: string | null,
): Promise<SheetSetResponse> {
  // Conditional spread: exactOptionalPropertyTypes forbids an explicit
  // `version: undefined` — absent and undefined are different things here.
  return api.sheets.list(projectId, version === undefined ? {} : { version });
}

export function fetchSheetSummary(projectId: string): Promise<SheetSetSummaryResponse> {
  return api.sheets.summary(projectId);
}

export function fetchSheetContent(
  projectId: string,
  sheetId: string,
  signal?: AbortSignal,
): Promise<SheetContentResponse> {
  return api.sheets.content(projectId, sheetId, signal === undefined ? {} : { signal });
}

/** A short-lived signed link for one sheet in one format (§11, §13). */
export function sheetDownloadLink(
  projectId: string,
  sheetId: string,
  format: 'svg' | 'dxf' | 'pdf',
): Promise<DownloadLink> {
  return api.sheets.download(projectId, sheetId, format);
}

export function fetchReviewTray(projectId: string, reconcile = true): Promise<ReviewTrayResponse> {
  return api.sheets.reviewTray(projectId, { reconcile });
}

export function fetchDrawingPreferences(): Promise<DrawingPreferencesResponse> {
  return api.sheets.preferences();
}

export function saveDrawingPreferences(
  input: Omit<DrawingPreferencesResponse, 'source' | 'firmLogoUrl'>,
): Promise<DrawingPreferencesResponse> {
  return api.sheets.savePreferences(input as unknown as Record<string, unknown>);
}

// ---------------------------------------------------------------------------
// Annotation ops (op 32) — the only annotation write path
// ---------------------------------------------------------------------------

/**
 * Re-attach an orphaned note to an element the architect picked.
 *
 * Exact id, chosen by a human. There is no fuzzy matching in MVP (D13) and this is
 * the function that would have to change to add it, so the promise and the code sit
 * next to each other.
 */
export function reattachAnnotationOp(modelAnnotationId: string, anchorElementId: string): Op {
  return {
    type: 'annotation.set',
    payload: {
      action: 'edit',
      id: modelAnnotationId,
      anchorElementId,
      orphaned: false,
    },
  } as Op;
}

export function deleteAnnotationOp(modelAnnotationId: string): Op {
  return {
    type: 'annotation.set',
    payload: { action: 'delete', id: modelAnnotationId },
  } as Op;
}

/** The note's text, however it was authored. Empty string when there is none. */
export function annotationText(annotation: { payload: Record<string, unknown> }): string {
  const raw = annotation.payload.text;
  return typeof raw === 'string' ? raw : '';
}

/**
 * A human label for an element id (`wall_01J…` → "Wall 01J…"). The picker shows this;
 * it deliberately does NOT try to name the element from the model, because the model
 * store may hold a different version than the sheet was drawn from, and a confidently
 * wrong label ("Bedroom 2") is worse than an honest id.
 */
export function elementLabel(elementId: string): string {
  const underscore = elementId.indexOf('_');
  if (underscore <= 0) return elementId;
  const kind = elementId.slice(0, underscore);
  const suffix = elementId.slice(underscore + 1);
  const pretty = kind.charAt(0).toUpperCase() + kind.slice(1);
  return `${pretty} · ${suffix.slice(-6)}`;
}
