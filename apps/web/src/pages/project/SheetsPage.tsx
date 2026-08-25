/**
 * SheetsPage — the Sheets tab's route entry (F7-A, playbook Phase 8).
 *
 * The screen itself is `features/sheets/SheetsTab`. This file stays a one-line
 * boundary for the same reason the other tabs do: the route table imports a page,
 * and a page that *is* the feature makes the feature impossible to reuse (the Phase 9
 * share viewer needs the sheet viewer without the generate button).
 */

export { SheetsTab as SheetsPage, SheetsTab as default } from '../../features/sheets';
