/**
 * store.ts — whether the palette and the cheatsheet are open, and what is typed.
 *
 * Zustand and a module store, for the reason `features/layers/store.ts` states:
 * the writers and the readers cannot see each other. The key listener writes
 * from a `document` handler with no component in scope; the palette reads from
 * a React tree portalled to `<body>`; a feature elsewhere in the app may want
 * to open the palette from a button. Context spans none of that.
 *
 * The registry itself is NOT in here. Commands change on mount and unmount of
 * whole features, are read on every keystroke by a non-React listener, and must
 * not cause a render when they change identity — that is a plain observable
 * class (`registry.ts`), and this store holds only the two booleans and the
 * query string that genuinely are view state.
 *
 * ## Why `highlightedId` is an id and not an index
 *
 * The list under the cursor is re-ranked on every keystroke. An index survives
 * that and points at a different command; an id either still exists in the
 * results or does not, and the palette falls back to the first row. The
 * difference is whether Enter runs what you were looking at.
 */

import { create } from 'zustand';

export interface CommandUiState {
  paletteOpen: boolean;
  /** Raw text, exactly as typed. `search.ts` does the normalising. */
  query: string;
  /** Command id under the cursor, or null for "the first result". */
  highlightedId: string | null;
  cheatsheetOpen: boolean;

  openPalette: () => void;
  closePalette: () => void;
  togglePalette: () => void;
  setQuery: (query: string) => void;
  setHighlighted: (id: string | null) => void;

  openCheatsheet: () => void;
  closeCheatsheet: () => void;
  toggleCheatsheet: () => void;
}

export const useCommandUiStore = create<CommandUiState>()((set, get) => ({
  paletteOpen: false,
  query: '',
  highlightedId: null,
  cheatsheetOpen: false,

  // Opening always starts from an empty query. A palette that remembers the
  // last search makes the second use feel broken — you press the key, and the
  // thing you wanted is filtered out by a word you typed ten minutes ago.
  openPalette: () => set({ paletteOpen: true, query: '', highlightedId: null }),
  closePalette: () => set({ paletteOpen: false, query: '', highlightedId: null }),
  togglePalette: () => (get().paletteOpen ? get().closePalette() : get().openPalette()),

  // Typing moves the cursor back to the top result: after narrowing the list,
  // the row you had selected is rarely still the one you want.
  setQuery: (query) => set({ query, highlightedId: null }),
  setHighlighted: (id) => set({ highlightedId: id }),

  openCheatsheet: () => set({ cheatsheetOpen: true }),
  closeCheatsheet: () => set({ cheatsheetOpen: false }),
  toggleCheatsheet: () => set((s) => ({ cheatsheetOpen: !s.cheatsheetOpen })),
}));

export const selectPaletteOpen = (s: CommandUiState): boolean => s.paletteOpen;
export const selectCheatsheetOpen = (s: CommandUiState): boolean => s.cheatsheetOpen;
export const selectQuery = (s: CommandUiState): string => s.query;
