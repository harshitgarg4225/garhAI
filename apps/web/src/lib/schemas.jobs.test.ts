/**
 * The drawings worker's job rows must never masquerade as solver jobs.
 *
 * `ExportJobOut.kind` is the export kind ("sheets", "dxf", …). Parsed through the
 * generic job schema its unknown value was caught as "solver", the jobs store
 * subscribed the sheet job's progress to `/solver-jobs/:id/events`, and the Sheets
 * tab never learned the set was drawn. The rows below are the API's own shape.
 */

import { describe, expect, it } from 'vitest';

import { toUiKind } from '../stores/jobs';
import { eventPathFor } from './sse';
import { exportJobSchema, sheetSetSchema } from './schemas';

const SHEETS_ROW = {
  id: 'b66c97b1-6a16-42cf-ab1d-71629d283971',
  projectId: '6bf14c4d-f59a-4e96-8476-0eabcefeb4f5',
  kind: 'sheets',
  status: 'queued',
  progress: 0,
  designVersionId: '6d0cb24f-6669-42e0-bb31-06678973dc62',
  downloadUrl: null,
  error: null,
  eventsUrl: 'http://127.0.0.1:8000/api/v1/export-jobs/b66c97b1-6a16-42cf-ab1d-71629d283971/events',
  createdAt: '2026-09-07T01:38:35.000Z',
  updatedAt: '2026-09-07T01:38:35.000Z',
};

describe('exportJobSchema', () => {
  it('labels a sheet-set job as the drawings worker, with the export kind as its type', () => {
    const job = exportJobSchema.parse(SHEETS_ROW);
    expect(job.kind).toBe('drawings');
    expect(job.exportKind).toBe('sheets');
    expect(job.type).toBe('sheets');
    expect(toUiKind(job)).toBe('sheets');
  });

  it('labels a DXF export the same way and the store shows it as an export', () => {
    const job = exportJobSchema.parse({ ...SHEETS_ROW, kind: 'dxf' });
    expect(job.kind).toBe('drawings');
    expect(job.type).toBe('export.dxf');
    expect(toUiKind(job)).toBe('export');
  });

  it('never yields a solver job for a row the drawings worker owns', () => {
    for (const kind of ['sheets', 'pdf-set', 'dxf', 'gltf', 'png-pack']) {
      expect(toUiKind(exportJobSchema.parse({ ...SHEETS_ROW, kind }))).not.toBe('solver');
    }
  });

  it('keeps the queued job on the sheet-set response so the tab can track it', () => {
    const set = sheetSetSchema.parse({
      projectId: SHEETS_ROW.projectId,
      designVersionId: SHEETS_ROW.designVersionId,
      sheets: [],
      job: SHEETS_ROW,
      generatedAt: null,
    });
    expect(set.job?.kind).toBe('drawings');
    expect(set.job?.type).toBe('sheets');
  });
});

describe('eventPathFor', () => {
  it('streams drawings jobs from the export-jobs route the API mounts', () => {
    expect(eventPathFor('drawings', SHEETS_ROW.id)).toBe(`/export-jobs/${SHEETS_ROW.id}/events`);
    expect(eventPathFor('solver', 'j')).toBe('/solver-jobs/j/events');
    expect(eventPathFor('render', 'j')).toBe('/render-jobs/j/events');
  });

  it('agrees with the eventsUrl the server sends on the row', () => {
    const job = exportJobSchema.parse(SHEETS_ROW);
    expect(job.eventsUrl).toContain(eventPathFor('drawings', job.id));
  });
});
