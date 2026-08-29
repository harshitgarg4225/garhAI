/**
 * Geometric constraints (C-3) — parallel, perpendicular, collinear, equal length,
 * and the two axis alignments.
 *
 * The solve itself is `solveConstraint` in `@garh/model`. This feature is the app's
 * half: reading the selection, dispatching through the model store, and saying what
 * happened.
 */

export { ConstraintBar } from './ConstraintBar';
export { canRunConstraint, requiredWalls, runConstraint, selectedWallIds } from './actions';
export { constraintCommands } from './commands';
