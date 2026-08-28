/**
 * pythonDefs.ts — read `services/drawings/render/hatch_patterns.py` and hand
 * back its `HATCH_DEFS` as plain data.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * TEST-ONLY. This module imports `node:fs`.
 * ════════════════════════════════════════════════════════════════════════════
 * Nothing in the app imports it (and `index.ts` deliberately does not export
 * it) — it exists so `patterns.drift.test.ts` can compare `patterns.ts`
 * against the file it mirrors, and so `geometry.test.ts` can derive its
 * expectations from the PYTHON angles rather than from the same TS table the
 * code under test reads. An assertion sourced from the thing being tested is
 * the "test that cannot fail" this repo has already shipped once.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY PARSE THE SOURCE INSTEAD OF RUNNING PYTHON
 * ════════════════════════════════════════════════════════════════════════════
 * `spawnSync('python3', …)` would be a shorter route to the same data, and it
 * would also make this gate depend on a Python interpreter existing in the
 * vitest job — CI's `unit (vitest)` job sets up Node and pnpm and nothing else
 * (.github/workflows/ci.yml). A gate that quietly skips when its interpreter
 * is missing is worse than no gate, so the parser is here in TS: it runs
 * wherever vitest runs, or it throws.
 *
 * The parser is deliberately narrow. `HATCH_DEFS` is a dict of `HatchDef(…)`
 * calls holding `PatternLine(…)` calls holding numbers, strings and tuples —
 * plain data with no expressions — so a tokeniser plus a recursive-descent
 * literal reader is enough. Anything it does not recognise (an arithmetic
 * expression, an f-string, a comprehension) THROWS with the offending text.
 * That is the desired behaviour: if the Python table stops being plain data,
 * the mirror needs a human, and a loud failure is how a human is called.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/** One `PatternLine(...)` row, in the mirror's own field names. */
export interface PythonHatchLine {
  readonly angleDeg: number;
  readonly base: readonly [number, number];
  readonly offset: readonly [number, number];
  readonly dashes: readonly number[];
}

/** One `HatchDef(...)` entry of `HATCH_DEFS`, in dict order. */
export interface PythonHatchDef {
  readonly key: string;
  readonly acadName: string;
  readonly label: string;
  readonly lines: readonly PythonHatchLine[];
}

/**
 * The pattern library, five directories up.
 *
 * NOT `new URL(rel, import.meta.url)`: Vite statically rewrites that idiom into
 * an `/@fs/` http asset URL, which `fileURLToPath` then rejects under the jsdom
 * environment (`kits.fixture.test.ts` hit this first). Converting to a path
 * first dodges the rewrite.
 */
export const HATCH_PATTERNS_PY = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../../../services/drawings/render/hatch_patterns.py',
);

/** The pattern library's source text. Throws if it has moved. */
export function readHatchPatternsSource(): string {
  return readFileSync(HATCH_PATTERNS_PY, 'utf8');
}

/** `HATCH_DEFS` from the real file, parsed. The one call a spec makes. */
export function readPythonHatchDefs(): PythonHatchDef[] {
  return parseHatchDefs(readHatchPatternsSource());
}

// ---------------------------------------------------------------------------
// Tokeniser
// ---------------------------------------------------------------------------

type Token =
  | { readonly kind: 'punct'; readonly text: string; readonly at: number }
  | { readonly kind: 'name'; readonly text: string; readonly at: number }
  | { readonly kind: 'number'; readonly value: number; readonly at: number }
  | { readonly kind: 'string'; readonly value: string; readonly at: number };

const PUNCT = new Set(['{', '}', '(', ')', '[', ']', ',', ':', '=']);
/** Python number literals as this table writes them: `-42`, `1.5`, `4_000`, `1e-9`. */
const NUMBER_RE = /-?(?:\d[\d_]*)(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?/y;
const NAME_RE = /[A-Za-z_][A-Za-z0-9_]*/y;

/**
 * Tokens of the ONE bracketed literal starting at `from`.
 *
 * Bounded rather than whole-file: everything after `HATCH_DEFS` is ordinary
 * Python (`def`, `->`, docstrings) that this reader has no business parsing,
 * and reading past the table's closing brace is how it would end up trying.
 * Depth returns to zero exactly once — at the `}` that closes the dict.
 */
function tokenise(source: string, from: number): Token[] {
  const tokens: Token[] = [];
  let depth = 0;
  let i = from;
  while (i < source.length) {
    const ch = source.charAt(i);
    if (ch === ' ' || ch === '\n' || ch === '\r' || ch === '\t') {
      i += 1;
      continue;
    }
    if (ch === '#') {
      // A comment can sit inside the literal (`#:` doc comments do, above it).
      const end = source.indexOf('\n', i);
      i = end === -1 ? source.length : end + 1;
      continue;
    }
    if (ch === '"' || ch === "'") {
      const { value, next } = readString(source, i);
      tokens.push({ kind: 'string', value, at: i });
      i = next;
      continue;
    }
    if (PUNCT.has(ch)) {
      tokens.push({ kind: 'punct', text: ch, at: i });
      i += 1;
      if (ch === '{' || ch === '(' || ch === '[') depth += 1;
      if (ch === '}' || ch === ')' || ch === ']') {
        depth -= 1;
        if (depth <= 0) return tokens;
      }
      continue;
    }
    NUMBER_RE.lastIndex = i;
    const number = NUMBER_RE.exec(source);
    // A leading `-` with no digits after it is not a number; fall through so
    // the parser reports the real problem rather than reading `-` as 0.
    if (number !== null && number[0] !== '-') {
      tokens.push({ kind: 'number', value: Number(number[0].replace(/_/g, '')), at: i });
      i = NUMBER_RE.lastIndex;
      continue;
    }
    NAME_RE.lastIndex = i;
    const name = NAME_RE.exec(source);
    if (name !== null) {
      tokens.push({ kind: 'name', text: name[0], at: i });
      i = NAME_RE.lastIndex;
      continue;
    }
    throw new Error(`hatch_patterns.py: cannot tokenise ${JSON.stringify(context(source, i))}`);
  }
  return tokens;
}

function readString(source: string, start: number): { value: string; next: number } {
  const quote = source.charAt(start);
  let out = '';
  let i = start + 1;
  while (i < source.length) {
    const ch = source.charAt(i);
    if (ch === '\\') {
      // No escape in this table today; handled so one does not silently
      // truncate a label into a passing comparison.
      out += source.charAt(i + 1);
      i += 2;
      continue;
    }
    if (ch === quote) return { value: out, next: i + 1 };
    out += ch;
    i += 1;
  }
  throw new Error(`hatch_patterns.py: unterminated string at ${String(start)}`);
}

function context(source: string, at: number): string {
  return source.slice(at, at + 40);
}

// ---------------------------------------------------------------------------
// Parser — literals, tuples, dicts and `Name(...)` calls, and nothing else
// ---------------------------------------------------------------------------

interface PyCall {
  readonly call: string;
  readonly args: readonly PyValue[];
  readonly kwargs: ReadonlyMap<string, PyValue>;
}
type PyValue = number | string | readonly PyValue[] | PyCall | ReadonlyMap<string, PyValue>;

class Reader {
  private index = 0;

  constructor(
    private readonly tokens: readonly Token[],
    private readonly constants: ReadonlyMap<string, string>,
  ) {}

  peek(): Token | undefined {
    return this.tokens[this.index];
  }

  next(): Token {
    const token = this.tokens[this.index];
    if (token === undefined) throw new Error('hatch_patterns.py: unexpected end of literal');
    this.index += 1;
    return token;
  }

  expect(text: string): void {
    const token = this.next();
    if (token.kind !== 'punct' || token.text !== text) {
      throw new Error(
        `hatch_patterns.py: expected ${text} at offset ${String(token.at)}, got ${describe(token)}`,
      );
    }
  }

  /** True when the next token is this punctuation; consumes it if so. */
  eat(text: string): boolean {
    const token = this.peek();
    if (token !== undefined && token.kind === 'punct' && token.text === text) {
      this.index += 1;
      return true;
    }
    return false;
  }

  value(): PyValue {
    const token = this.next();
    if (token.kind === 'number') return token.value;
    if (token.kind === 'string') return token.value;
    if (token.kind === 'punct' && token.text === '{') return this.dict();
    if (token.kind === 'punct' && (token.text === '(' || token.text === '[')) {
      return this.sequence(token.text === '(' ? ')' : ']');
    }
    if (token.kind === 'name') {
      if (this.eat('(')) return this.call(token.text);
      const constant = this.constants.get(token.text);
      if (constant === undefined) {
        throw new Error(
          `hatch_patterns.py: ${token.text} is not a module-level string constant this reader ` +
            'knows. The mirror cannot be checked against an expression it cannot evaluate.',
        );
      }
      return constant;
    }
    throw new Error(`hatch_patterns.py: unexpected ${describe(token)}`);
  }

  private dict(): ReadonlyMap<string, PyValue> {
    const out = new Map<string, PyValue>();
    while (!this.eat('}')) {
      const key = this.value();
      if (typeof key !== 'string') {
        throw new Error('hatch_patterns.py: HATCH_DEFS keys must be strings');
      }
      this.expect(':');
      out.set(key, this.value());
      if (!this.eat(',')) {
        this.expect('}');
        break;
      }
    }
    return out;
  }

  private sequence(close: string): readonly PyValue[] {
    const out: PyValue[] = [];
    while (!this.eat(close)) {
      out.push(this.value());
      if (!this.eat(',')) {
        this.expect(close);
        break;
      }
    }
    return out;
  }

  private call(name: string): PyCall {
    const args: PyValue[] = [];
    const kwargs = new Map<string, PyValue>();
    while (!this.eat(')')) {
      const token = this.peek();
      const after = this.tokens[this.index + 1];
      if (
        token !== undefined &&
        token.kind === 'name' &&
        after !== undefined &&
        after.kind === 'punct' &&
        after.text === '='
      ) {
        this.index += 2;
        kwargs.set(token.text, this.value());
      } else {
        args.push(this.value());
      }
      if (!this.eat(',')) {
        this.expect(')');
        break;
      }
    }
    return { call: name, args, kwargs };
  }
}

function describe(token: Token): string {
  switch (token.kind) {
    case 'punct':
    case 'name':
      return `${token.kind} ${token.text}`;
    case 'number':
      return `number ${String(token.value)}`;
    case 'string':
      return `string ${JSON.stringify(token.value)}`;
  }
}

// ---------------------------------------------------------------------------
// HATCH_DEFS → plain data
// ---------------------------------------------------------------------------

/** Module-level `NAME = "value"` constants, so `key=SOLID_KEY` resolves. */
function stringConstants(source: string): ReadonlyMap<string, string> {
  const out = new Map<string, string>();
  const re = /^([A-Z][A-Z0-9_]*)\s*(?::\s*[^=\n]+)?=\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*$/gm;
  for (const match of source.matchAll(re)) {
    const name = match[1];
    const literal = match[2];
    if (name === undefined || literal === undefined) continue;
    out.set(name, literal.slice(1, -1));
  }
  return out;
}

/**
 * `Array.isArray` widens to `any[]`, which the workspace's type-aware lint
 * rightly rejects at every downstream call. One guard, typed, keeps the rest of
 * the reader honest.
 */
function isSequence(value: PyValue): value is readonly PyValue[] {
  return Array.isArray(value);
}

/** Same reason as `isSequence`: `instanceof Map` alone narrows to `Map<any, any>`. */
function isDict(value: PyValue): value is ReadonlyMap<string, PyValue> {
  return value instanceof Map;
}

function asCall(value: PyValue, expected: string): PyCall {
  if (
    typeof value !== 'object' ||
    value === null ||
    isSequence(value) ||
    value instanceof Map ||
    !('call' in value)
  ) {
    throw new Error(`hatch_patterns.py: expected a ${expected}(...) call`);
  }
  if (value.call !== expected) {
    throw new Error(`hatch_patterns.py: expected ${expected}(...), got ${value.call}(...)`);
  }
  return value;
}

function argument(call: PyCall, name: string, position: number): PyValue {
  const keyword = call.kwargs.get(name);
  if (keyword !== undefined) return keyword;
  const positional = call.args[position];
  if (positional === undefined) {
    throw new Error(`hatch_patterns.py: ${call.call}(...) is missing ${name}`);
  }
  return positional;
}

function asString(value: PyValue, what: string): string {
  if (typeof value !== 'string') throw new Error(`hatch_patterns.py: ${what} is not a string`);
  return value;
}

function asNumber(value: PyValue, what: string): number {
  if (typeof value !== 'number') throw new Error(`hatch_patterns.py: ${what} is not a number`);
  return value;
}

function asNumbers(value: PyValue, what: string): number[] {
  if (!isSequence(value)) throw new Error(`hatch_patterns.py: ${what} is not a tuple`);
  return value.map((item) => asNumber(item, what));
}

function asPair(value: PyValue, what: string): [number, number] {
  const numbers = asNumbers(value, what);
  if (numbers.length !== 2) {
    throw new Error(`hatch_patterns.py: ${what} has ${String(numbers.length)} values, expected 2`);
  }
  // `noUncheckedIndexedAccess`: the length check above is not visible to the
  // compiler, so read through a destructure it can prove.
  const [x, y] = numbers as [number, number];
  return [x, y];
}

/** Parse a `hatch_patterns.py` source into its `HATCH_DEFS`, in dict order. */
export function parseHatchDefs(source: string): PythonHatchDef[] {
  const assignment = /^HATCH_DEFS[^=\n]*=\s*\{/m.exec(source);
  if (assignment === null) {
    throw new Error('hatch_patterns.py: no `HATCH_DEFS = {` assignment found');
  }
  // Start the reader ON the `{` so `value()` reads the dict literal.
  const start = assignment.index + assignment[0].length - 1;
  const reader = new Reader(tokenise(source, start), stringConstants(source));
  const table = reader.value();
  if (!isDict(table)) throw new Error('hatch_patterns.py: HATCH_DEFS is not a dict');

  const defs: PythonHatchDef[] = [];
  for (const [dictKey, entry] of table) {
    const def = asCall(entry, 'HatchDef');
    const lines = argument(def, 'lines', 3);
    if (!isSequence(lines)) {
      throw new Error(`hatch_patterns.py: ${dictKey}.lines is not a tuple`);
    }
    defs.push({
      key: asString(argument(def, 'key', 0), `${dictKey}.key`),
      acadName: asString(argument(def, 'acad_name', 1), `${dictKey}.acad_name`),
      label: asString(argument(def, 'label', 2), `${dictKey}.label`),
      lines: lines.map((raw) => {
        const line = asCall(raw, 'PatternLine');
        const dashes = line.kwargs.get('dashes') ?? line.args[3] ?? [];
        return {
          angleDeg: asNumber(argument(line, 'angle_deg', 0), `${dictKey}.angle_deg`),
          base: asPair(argument(line, 'base', 1), `${dictKey}.base`),
          offset: asPair(argument(line, 'offset', 2), `${dictKey}.offset`),
          dashes: asNumbers(dashes, `${dictKey}.dashes`),
        };
      }),
    });
    if (defs[defs.length - 1]?.key !== dictKey) {
      throw new Error(
        `hatch_patterns.py: entry filed under ${dictKey} carries key ` +
          `${String(defs[defs.length - 1]?.key)} — the dict key and the HatchDef disagree`,
      );
    }
  }
  return defs;
}
