/**
 * cn() — class-name joiner with last-wins conflict resolution.
 *
 * WHY THIS EXISTS INSTEAD OF `clsx` + `tailwind-merge`
 * ---------------------------------------------------------------------------
 * Both are MIT and would be allowed by the licence rules, but adding a runtime
 * dependency to the shared UI package costs a `DECISIONS.md` row (repo root,
 * owned elsewhere) and ~6kB in the initial bundle that §14 budgets tightly.
 * The behaviour we actually need is small and testable, so it lives here.
 *
 * The problem it solves: in Tailwind, `class="p-2 p-4"` does NOT resolve to
 * `p-4`. The winner is whichever rule appears later in the generated
 * stylesheet, which is alphabetical-ish and unrelated to attribute order. So a
 * component that writes `cn('px-3 py-2', props.className)` cannot be overridden
 * by a caller passing `px-6` unless we drop the losing class ourselves.
 *
 * HOW IT WORKS: each class is reduced to a "conflict key" = its variant prefix
 * chain (`hover:`, `dark:`, `md:`, `[&>svg]:`) plus the utility group it
 * belongs to (`p`, `px`, `bg`, `text-size`, …). Later classes evict earlier
 * ones with the same key. Classes we do not recognise are always kept and never
 * evict anything — unknown input degrades to a plain join, which is safe.
 *
 * DOCUMENTED LIMITS (deliberate, not bugs):
 *  - Only the groups in GROUPS below are de-duplicated. Others accumulate.
 *  - `p-4` does not evict `px-2` (Tailwind's own shorthand/longhand
 *    interaction). Write the longhand you mean.
 *  - Arbitrary values (`w-[37px]`) are grouped by their prefix, which is right.
 */

export type ClassValue =
  | string
  | number
  | null
  | undefined
  | false
  | ClassValue[]
  | Record<string, boolean | null | undefined>;

/**
 * Utility-group table. Order matters: the FIRST prefix that matches wins, so
 * longer/more specific prefixes must come before their shorter parents
 * (`text-` after `text-align` style pseudo-groups, `border-x-` before
 * `border-`). Each entry is `[prefix, groupKey]`.
 */
const GROUPS: readonly (readonly [string, string])[] = [
  // spacing — longhands before shorthands
  ['px-', 'px'],
  ['py-', 'py'],
  ['pt-', 'pt'],
  ['pr-', 'pr'],
  ['pb-', 'pb'],
  ['pl-', 'pl'],
  ['p-', 'p'],
  ['mx-', 'mx'],
  ['my-', 'my'],
  ['mt-', 'mt'],
  ['mr-', 'mr'],
  ['mb-', 'mb'],
  ['ml-', 'ml'],
  ['m-', 'm'],
  ['gap-x-', 'gap-x'],
  ['gap-y-', 'gap-y'],
  ['gap-', 'gap'],
  ['space-x-', 'space-x'],
  ['space-y-', 'space-y'],

  // sizing
  ['min-w-', 'min-w'],
  ['max-w-', 'max-w'],
  ['min-h-', 'min-h'],
  ['max-h-', 'max-h'],
  ['w-', 'w'],
  ['h-', 'h'],
  ['size-', 'size'],

  // typography
  ['font-', 'font'],
  ['tracking-', 'tracking'],
  ['leading-', 'leading'],
  ['text-left', 'text-align'],
  ['text-center', 'text-align'],
  ['text-right', 'text-align'],
  ['text-justify', 'text-align'],
  ['truncate', 'text-overflow'],
  ['text-ellipsis', 'text-overflow'],
  ['text-clip', 'text-overflow'],
  ['whitespace-', 'whitespace'],
  ['text-', 'text'], // colour AND size share a prefix; see resolveTextGroup

  // colour & decoration
  ['bg-', 'bg'],
  ['fill-', 'fill'],
  ['stroke-', 'stroke'],
  ['shadow-', 'shadow'],
  ['opacity-', 'opacity'],
  ['ring-offset-', 'ring-offset'],
  ['ring-', 'ring'],
  ['outline-', 'outline'],

  // borders
  ['rounded-t-', 'rounded-t'],
  ['rounded-b-', 'rounded-b'],
  ['rounded-l-', 'rounded-l'],
  ['rounded-r-', 'rounded-r'],
  ['rounded', 'rounded'],
  ['border-x-', 'border-x'],
  ['border-y-', 'border-y'],
  ['border-t-', 'border-t'],
  ['border-r-', 'border-r'],
  ['border-b-', 'border-b'],
  ['border-l-', 'border-l'],
  ['border-', 'border'],

  // layout
  ['grid-cols-', 'grid-cols'],
  ['grid-rows-', 'grid-rows'],
  ['col-span-', 'col-span'],
  ['row-span-', 'row-span'],
  // `flex-*` is FOUR distinct CSS properties, not one group. A single
  // `['flex-', 'flex']` entry made `flex-col` evict `flex-1` (direction vs
  // flex sizing), which silently collapsed any flex-1 column passed through a
  // component's className — found when the share viewer's canvas rendered at
  // height 0. Longer prefixes first: first match wins.
  ['flex-row', 'flex-direction'],
  ['flex-col', 'flex-direction'],
  ['flex-wrap', 'flex-wrap'],
  ['flex-nowrap', 'flex-wrap'],
  ['flex-grow', 'grow'],
  ['flex-shrink', 'shrink'],
  ['flex-', 'flex'],
  ['basis-', 'basis'],
  ['grow', 'grow'],
  ['shrink', 'shrink'],
  ['order-', 'order'],
  ['items-', 'items'],
  ['justify-', 'justify'],
  ['self-', 'self'],
  ['content-', 'content-align'],
  ['place-', 'place'],
  ['overflow-x-', 'overflow-x'],
  ['overflow-y-', 'overflow-y'],
  ['overflow-', 'overflow'],
  ['top-', 'top'],
  ['right-', 'right'],
  ['bottom-', 'bottom'],
  ['left-', 'left'],
  ['inset-', 'inset'],
  ['z-', 'z'],
  ['cursor-', 'cursor'],
  ['pointer-events-', 'pointer-events'],
  ['select-', 'select'],
  ['transition', 'transition'],
  ['duration-', 'duration'],
  ['ease-', 'ease'],
  ['animate-', 'animate'],
];

/** Bare (value-less) utilities that conflict with each other. */
const BARE_GROUPS: Readonly<Record<string, string>> = {
  block: 'display',
  'inline-block': 'display',
  inline: 'display',
  flex: 'display',
  'inline-flex': 'display',
  grid: 'display',
  'inline-grid': 'display',
  contents: 'display',
  hidden: 'display',
  table: 'display',
  static: 'position',
  fixed: 'position',
  absolute: 'position',
  relative: 'position',
  sticky: 'position',
  italic: 'font-style',
  'not-italic': 'font-style',
  underline: 'text-decoration',
  'line-through': 'text-decoration',
  'no-underline': 'text-decoration',
  uppercase: 'text-transform',
  lowercase: 'text-transform',
  capitalize: 'text-transform',
  'normal-case': 'text-transform',
};

/** Tailwind's font sizes — needed to tell `text-sm` (size) from `text-ink` (colour). */
const TEXT_SIZES = new Set([
  '2xs',
  'xs',
  'sm',
  'base',
  'lg',
  'xl',
  '2xl',
  '3xl',
  '4xl',
  '5xl',
  '6xl',
  '7xl',
  '8xl',
  '9xl',
]);

function resolveTextGroup(rest: string): string {
  const head = rest.split('/')[0] ?? rest;
  return TEXT_SIZES.has(head) ? 'text-size' : 'text-color';
}

/**
 * Split a class into `[variantPrefix, base]`, e.g.
 * `dark:hover:bg-brand` -> `['dark:hover:', 'bg-brand']`.
 * Colons inside arbitrary values (`[&:hover]:x`) are respected by only
 * splitting on colons that are outside brackets.
 */
function splitVariants(token: string): [string, string] {
  let depth = 0;
  let lastColon = -1;
  for (let i = 0; i < token.length; i += 1) {
    const ch = token[i];
    if (ch === '[' || ch === '(') depth += 1;
    else if (ch === ']' || ch === ')') depth -= 1;
    else if (ch === ':' && depth === 0) lastColon = i;
  }
  if (lastColon === -1) return ['', token];
  return [token.slice(0, lastColon + 1), token.slice(lastColon + 1)];
}

/** Conflict key for a class, or `null` when the class should never evict. */
function conflictKey(token: string): string | null {
  const [variants, raw] = splitVariants(token);
  const base = raw.startsWith('!') ? raw.slice(1) : raw;
  const negated = base.startsWith('-');
  const body = negated ? base.slice(1) : base;

  const bare = BARE_GROUPS[body];
  if (bare !== undefined) return `${variants}${bare}`;

  for (const [prefix, group] of GROUPS) {
    if (!body.startsWith(prefix)) continue;
    if (group === 'text') return `${variants}${resolveTextGroup(body.slice(prefix.length))}`;
    return `${variants}${group}`;
  }
  return null;
}

function push(out: string[], value: ClassValue): void {
  if (value === null || value === undefined || value === false || value === '') return;
  if (typeof value === 'string') {
    for (const tok of value.split(/\s+/)) if (tok !== '') out.push(tok);
    return;
  }
  if (typeof value === 'number') {
    out.push(String(value));
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) push(out, item);
    return;
  }
  for (const [key, on] of Object.entries(value)) {
    if (on) push(out, key);
  }
}

/**
 * Join class values, dropping earlier classes that a later class overrides.
 *
 * ```ts
 * cn('px-3 py-2 text-sm', condition && 'text-ink-muted', props.className)
 * // caller passing `px-6` really does get px-6
 * ```
 */
export function cn(...inputs: ClassValue[]): string {
  const tokens: string[] = [];
  push(tokens, inputs);

  const keep: string[] = [];
  const seen = new Map<string, number>(); // conflict key -> index in `keep`
  for (const token of tokens) {
    const key = conflictKey(token);
    if (key === null) {
      keep.push(token);
      continue;
    }
    const at = seen.get(key);
    if (at === undefined) {
      seen.set(key, keep.length);
      keep.push(token);
    } else {
      keep[at] = token; // later wins, position preserved for readability
    }
  }
  return keep.join(' ');
}
