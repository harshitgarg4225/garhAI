/**
 * The line under a failed job must not blame the brief for a fault of ours.
 *
 * The API folds the worker's own action into the message ("… Try again — if it
 * keeps happening, contact support."). The first trial architect saw that AND
 * "loosen a brief requirement" under it for a crash in our worker image.
 */

import { describe, expect, it } from 'vitest';

import { solverAdvice } from './jobs';

describe('solverAdvice', () => {
  it('defers to the worker when it already said what to do', () => {
    expect(
      solverAdvice(
        'solver',
        'Something went wrong on our side while running this job. Try again — if it keeps happening, contact support.',
      ),
    ).toBe('Nothing in your design was changed.');
  });

  it('suggests loosening the brief only when the worker gave no next step', () => {
    expect(solverAdvice('solver', 'The solver did not finish.')).toContain(
      'loosen a brief requirement',
    );
    expect(solverAdvice('solver', null)).toContain('loosen a brief requirement');
  });

  it('never tells a render or export to loosen the brief', () => {
    expect(solverAdvice('render', null)).not.toContain('brief');
  });
});
