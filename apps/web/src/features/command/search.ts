/**
 * search.ts — the palette's fuzzy match, and the grouping the palette renders.
 *
 * Subsequence matching, not substring: an architect types "dw" for "Draw
 * walls" and "expdxf" for "Export DXF", and a substring filter answers neither.
 * Scoring rewards the three things that make a fuzzy hit feel intentional —
 * matching at the start of the string, matching at the start of a word, and
 * matching several characters in a row — and penalises the distance skipped
 * between hits, which is what stops "sheets" ranking above "Set snap" for "ss".
 *
 * Everything here is pure and deterministic. No `Math.random`, no `Date`, no
 * locale-dependent comparison: the same query over the same registry gives the
 * same order on every machine, which is the only way the tests can assert an
 * order at all.
 *
 * The returned ranges are half-open `[start, end)` offsets into the command's
 * TITLE, so the palette can bold the matched characters. A keyword or group hit
 * returns no ranges — there is nothing in the title to bold, and inventing
 * highlights for characters that did not match is worse than none.
 */

import { COMMAND_GROUPS, type Command, type CommandGroup } from './types';

/** Half-open `[start, end)`. */
export type MatchRange = readonly [number, number];

export interface CommandMatch {
  readonly command: Command;
  readonly score: number;
  readonly ranges: readonly MatchRange[];
}

export interface CommandGroupResult {
  readonly group: CommandGroup;
  readonly matches: readonly CommandMatch[];
}

/** Characters after which the next character starts a new "word". */
const BOUNDARY = /[\s._\-/(:]/;

const SCORE_PER_CHAR = 4;
const SCORE_CONSECUTIVE = 8;
const SCORE_WORD_START = 6;
const SCORE_STRING_START = 10;
/** Cost per character skipped, capped so one long gap is not fatal. */
const PENALTY_PER_GAP = 0.5;
const MAX_GAP_PENALTY = 6;
/** Keyword and group hits are real, but a title hit should always beat them. */
const KEYWORD_WEIGHT = 0.55;
const GROUP_WEIGHT = 0.3;

interface RawMatch {
  readonly score: number;
  readonly ranges: readonly MatchRange[];
}

/**
 * Best subsequence match of `needle` in `haystack`, both already lower-cased.
 *
 * Tries every position the first needle character occurs at and keeps the
 * highest-scoring greedy run from each. Greedy-from-the-left alone gets the
 * ranges wrong on titles that repeat a letter ("Show or hide the grid" for
 * "hide"), and the strings here are short enough that trying each start costs
 * nothing measurable.
 */
export function fuzzyMatch(haystack: string, needle: string): RawMatch | null {
  if (needle === '') return { score: 0, ranges: [] };
  if (needle.length > haystack.length) return null;

  let best: RawMatch | null = null;
  for (let start = 0; start <= haystack.length - needle.length; start += 1) {
    if (haystack[start] !== needle[0]) continue;
    const attempt = greedyFrom(haystack, needle, start);
    if (attempt === null) break; // no later start can succeed either
    if (best === null || attempt.score > best.score) best = attempt;
  }
  return best;
}

function greedyFrom(haystack: string, needle: string, start: number): RawMatch | null {
  const ranges: MatchRange[] = [];
  let score = 0;
  let cursor = start;
  let previous = -2;

  for (const wanted of needle) {
    const at = haystack.indexOf(wanted, cursor);
    if (at < 0) return null;

    score += SCORE_PER_CHAR;
    if (at === previous + 1) {
      score += SCORE_CONSECUTIVE;
      const last = ranges[ranges.length - 1];
      if (last !== undefined) ranges[ranges.length - 1] = [last[0], at + 1];
      else ranges.push([at, at + 1]);
    } else {
      if (at === 0) score += SCORE_STRING_START;
      else if (BOUNDARY.test(haystack[at - 1] ?? '')) score += SCORE_WORD_START;
      score -= Math.min((at - cursor) * PENALTY_PER_GAP, MAX_GAP_PENALTY);
      ranges.push([at, at + 1]);
    }

    previous = at;
    cursor = at + 1;
  }

  // Shorter titles win ties: "Undo" should outrank "Undo the last change" when
  // both match equally well, and a long sentence should not win on length.
  return { score: score - haystack.length * 0.08, ranges };
}

/**
 * Strip the query down to what actually participates in matching.
 *
 * Whitespace goes: someone typing "draw wall" means the same as "drawwall", and
 * keeping the space would force the title's own space to line up with it.
 */
export function normaliseQuery(query: string): string {
  return query.toLowerCase().replace(/\s+/g, '');
}

/**
 * Score one command against a normalised query.
 *
 * Tries the title, then the keywords, then the group name, and keeps the best —
 * so "help" finds the cheatsheet through its group and "⌘k" finds nothing,
 * which is correct: a binding is not a search term, it is displayed on the row.
 */
export function scoreCommand(command: Command, needle: string): CommandMatch | null {
  const title = fuzzyMatch(command.title.toLowerCase(), needle);
  let best: CommandMatch | null =
    title === null ? null : { command, score: title.score, ranges: title.ranges };

  for (const keyword of command.keywords ?? []) {
    const hit = fuzzyMatch(keyword.toLowerCase(), needle);
    if (hit === null) continue;
    const score = hit.score * KEYWORD_WEIGHT;
    if (best === null || score > best.score) best = { command, score, ranges: [] };
  }

  const group = fuzzyMatch(command.group.toLowerCase(), needle);
  if (group !== null) {
    const score = group.score * GROUP_WEIGHT;
    if (best === null || score > best.score) best = { command, score, ranges: [] };
  }

  return best;
}

/**
 * Rank commands for a query.
 *
 * An empty query returns everything in registry order — the registry already
 * sorts by group, so the palette's resting state is the full menu, which is how
 * a palette teaches someone what the app can do.
 */
export function searchCommands(
  commands: readonly Command[],
  query: string,
): readonly CommandMatch[] {
  const needle = normaliseQuery(query);
  if (needle === '') return commands.map((command) => ({ command, score: 0, ranges: [] }));

  const matches: CommandMatch[] = [];
  for (const command of commands) {
    const match = scoreCommand(command, needle);
    if (match !== null) matches.push(match);
  }

  // Stable within a score: the registry's own order is the tiebreak, so two
  // equally good hits do not swap places between keystrokes.
  const order = new Map(commands.map((command, index) => [command.id, index]));
  return matches.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return (order.get(a.command.id) ?? 0) - (order.get(b.command.id) ?? 0);
  });
}

/**
 * Lay ranked matches out in sections.
 *
 * While searching, groups are ordered by their strongest member rather than by
 * the fixed `COMMAND_GROUPS` order — otherwise the best hit in the app could
 * render below three weaker ones just because "Help" sorts last, and the first
 * row (which Enter runs) would not be the best answer. With no query there is
 * nothing to rank by, so the declared order stands.
 */
export function groupMatches(
  matches: readonly CommandMatch[],
  ranked: boolean,
): readonly CommandGroupResult[] {
  const byGroup = new Map<CommandGroup, CommandMatch[]>();
  for (const match of matches) {
    const list = byGroup.get(match.command.group);
    if (list === undefined) byGroup.set(match.command.group, [match]);
    else list.push(match);
  }

  const groups = [...byGroup.entries()].map(([group, list]) => ({ group, matches: list }));
  groups.sort((a, b) => {
    if (!ranked) return COMMAND_GROUPS.indexOf(a.group) - COMMAND_GROUPS.indexOf(b.group);
    const bestA = a.matches[0]?.score ?? 0;
    const bestB = b.matches[0]?.score ?? 0;
    if (bestA !== bestB) return bestB - bestA;
    return COMMAND_GROUPS.indexOf(a.group) - COMMAND_GROUPS.indexOf(b.group);
  });
  return groups;
}

/** The rendered order, flattened — arrow keys must walk exactly what is drawn. */
export function flattenGroups(groups: readonly CommandGroupResult[]): readonly CommandMatch[] {
  return groups.flatMap((group) => group.matches);
}
