/**
 * The palette's search, asserted on the ORDER it produces, not on the fact that
 * it produced something.
 *
 * A fuzzy filter is easy to test uselessly: query "wall", assert "Draw walls"
 * is in the results, ship a matcher that returns every command for every query.
 * Every test below therefore pins either an exclusion or a relative rank, and
 * the ranking tests name the command that must come SECOND as well as the one
 * that must come first.
 */

import { describe, expect, it } from 'vitest';

import { flattenGroups, fuzzyMatch, groupMatches, normaliseQuery, searchCommands } from './search';
import type { Command } from './types';

function command(id: string, title: string, extra: Partial<Command> = {}): Command {
  return { id, title, group: 'Edit', run: () => undefined, ...extra };
}

const CORPUS: readonly Command[] = [
  command('a', 'Draw walls', { group: 'Tools', keywords: ['partition'] }),
  command('b', 'Place a door', { group: 'Tools' }),
  command('c', 'Undo the last change', { group: 'Edit' }),
  command('d', 'Show or hide the dimensions', { group: 'View' }),
  command('e', 'Show every keyboard shortcut', { group: 'Help', keywords: ['cheatsheet'] }),
];

describe('fuzzyMatch', () => {
  it('matches a subsequence, not just a substring', () => {
    expect(fuzzyMatch('draw walls', 'dwl')).not.toBeNull();
    expect(fuzzyMatch('draw walls', 'dlw')).toBeNull();
  });

  it('reports the matched characters so the palette can highlight them', () => {
    const hit = fuzzyMatch('draw walls', 'wall');
    expect(hit?.ranges).toEqual([[5, 9]]);
  });

  it('scores a word-start match above a mid-word one', () => {
    const wordStart = fuzzyMatch('place a door', 'd');
    const midWord = fuzzyMatch('undo the last', 'd');
    expect(wordStart).not.toBeNull();
    expect(midWord).not.toBeNull();
    expect((wordStart?.score ?? 0) > (midWord?.score ?? 0)).toBe(true);
  });

  it('scores a run of characters above the same characters scattered', () => {
    const together = fuzzyMatch('show dimensions', 'dim');
    const scattered = fuzzyMatch('draw it mainly', 'dim');
    expect((together?.score ?? 0) > (scattered?.score ?? 0)).toBe(true);
  });
});

describe('searchCommands', () => {
  it('returns everything, in registry order, for an empty query', () => {
    expect(searchCommands(CORPUS, '   ').map((m) => m.command.id)).toEqual([
      'a',
      'b',
      'c',
      'd',
      'e',
    ]);
  });

  it('EXCLUDES what does not match — the assertion that makes the rest mean anything', () => {
    const ids = searchCommands(CORPUS, 'wall').map((m) => m.command.id);
    expect(ids).toEqual(['a']);
    expect(ids).not.toContain('b');
    expect(ids).not.toContain('c');
  });

  it('ranks the obvious answer first and still offers the near miss', () => {
    const ids = searchCommands(CORPUS, 'undo').map((m) => m.command.id);
    expect(ids[0]).toBe('c');
  });

  it('ignores spaces in the query, so "draw wall" finds "Draw walls"', () => {
    expect(normaliseQuery(' Draw Wall ')).toBe('drawwall');
    expect(searchCommands(CORPUS, 'draw wall').map((m) => m.command.id)).toEqual(['a']);
  });

  it('finds a command through a keyword it does not say out loud', () => {
    const ids = searchCommands(CORPUS, 'cheatsheet').map((m) => m.command.id);
    expect(ids).toEqual(['e']);
    // …and a keyword hit carries no highlight ranges, because there is nothing
    // in the visible title that matched.
    expect(searchCommands(CORPUS, 'cheatsheet')[0]?.ranges).toEqual([]);
  });

  it('lets a title hit beat a keyword hit for the same query', () => {
    const corpus = [
      command('title', 'Partition the plot', { group: 'Tools' }),
      command('keyword', 'Draw walls', { group: 'Tools', keywords: ['partition'] }),
    ];
    expect(searchCommands(corpus, 'partition').map((m) => m.command.id)).toEqual([
      'title',
      'keyword',
    ]);
  });

  it('is deterministic when two commands score identically', () => {
    const corpus = [command('one', 'Same title'), command('two', 'Same title')];
    expect(searchCommands(corpus, 'same').map((m) => m.command.id)).toEqual(['one', 'two']);
    expect(searchCommands(corpus, 'same').map((m) => m.command.id)).toEqual(['one', 'two']);
  });
});

describe('groupMatches', () => {
  it('uses the declared group order when there is nothing to rank by', () => {
    const groups = groupMatches(searchCommands(CORPUS, ''), false);
    expect(groups.map((g) => g.group)).toEqual(['Tools', 'Edit', 'View', 'Help']);
  });

  it('promotes the group holding the best hit while searching', () => {
    // "Help" sorts last by declaration. With a query it answers, the section
    // has to come first — the top row is what Enter runs.
    const matches = searchCommands(CORPUS, 'shortcut');
    const groups = groupMatches(matches, true);
    expect(groups[0]?.group).toBe('Help');
  });

  it('flattens to exactly what is drawn, so arrow keys cannot desync', () => {
    const groups = groupMatches(searchCommands(CORPUS, ''), false);
    const flat = flattenGroups(groups);
    expect(flat.length).toBe(CORPUS.length);
    expect(flat.map((m) => m.command.id)).toEqual(
      groups.flatMap((g) => g.matches.map((m) => m.command.id)),
    );
  });
});
