/**
 * commands.ts — the constraints in the ⌘K palette (C-3 × C-1).
 *
 * Registered through `useRegisterCommands` rather than added to `defaultCommands.ts`,
 * because that table is an exhaustive mirror of `lib/keymap.ts`'s `CommandId` union and
 * these carry no keyboard shortcut. A command with no key does not belong in a table
 * whose purpose is to prove every key is real.
 *
 * `enabled` is a live selection read, so a palette row for Parallel is greyed when only
 * one wall is selected. A palette that offers an action which then explains it cannot
 * run is a palette that lies about the state of the document.
 */

import type { ConstraintKind } from '@garh/model';

import type { Command } from '../command/types';
import { canRunConstraint, runConstraint } from './actions';

interface Spec {
  readonly kind: ConstraintKind;
  readonly title: string;
  readonly description: string;
  readonly keywords: readonly string[];
}

const SPECS: readonly Spec[] = [
  {
    kind: 'horizontal',
    title: 'Straighten horizontal',
    description: 'Turn the selected wall exactly horizontal, keeping its length.',
    keywords: ['horizontal', 'straighten', 'align', 'level', 'orthogonal'],
  },
  {
    kind: 'vertical',
    title: 'Straighten vertical',
    description: 'Turn the selected wall exactly vertical, keeping its length.',
    keywords: ['vertical', 'straighten', 'align', 'plumb', 'orthogonal'],
  },
  {
    kind: 'parallel',
    title: 'Make parallel',
    description: 'Turn the other selected walls to match the first one.',
    keywords: ['parallel', 'constrain', 'match angle'],
  },
  {
    kind: 'perpendicular',
    title: 'Make perpendicular',
    description: 'Turn the other selected walls square to the first one.',
    keywords: ['perpendicular', 'square', 'right angle', 'normal'],
  },
  {
    kind: 'collinear',
    title: 'Make collinear',
    description: 'Put the other selected walls on the first one’s line.',
    keywords: ['collinear', 'aligned', 'same line', 'in line'],
  },
  {
    kind: 'equal-length',
    title: 'Match lengths',
    description: 'Give the other selected walls the first one’s length.',
    keywords: ['equal', 'same length', 'match length', 'equalise'],
  },
];

export const constraintCommands: readonly Command[] = SPECS.map((spec) => ({
  id: `constraint.${spec.kind}`,
  title: spec.title,
  group: 'Edit',
  icon: 'ruler',
  keywords: spec.keywords,
  description: spec.description,
  enabled: () => canRunConstraint(spec.kind),
  run: () => void runConstraint(spec.kind),
}));
