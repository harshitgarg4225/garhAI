/**
 * @garh/model — the model core. THE CONTRACT every other package codes against.
 *
 * Read the modules in this order the first time:
 *   units     integer-mm boundary: parse anything an architect types, format out
 *   ids       `${type}_${ulid}` element identity (+ deterministic derived ids)
 *   geometry  integer-mm 2D primitives, with an explicit exactness contract
 *   model     the HouseModel document (§3) and the ProjectDoc the op log folds to
 *   ops       the 32-op taxonomy (§4) + OP_CATALOG (the copilot prompt source)
 *   validate  the fold invariants, with machine-readable rejection codes
 *   rooms     planar-subdivision room detection with id preservation
 *   fold      fold/replay/groups/undo + canonicalJson + stateHash
 *
 * The Python mirror lives at `apps/api/garh_model/` and MUST agree with this
 * package. The contract files are in `packages/model/schema/`:
 *   common.schema.json  house-model.schema.json  project-doc.schema.json
 *   ops.schema.json     validation-issue.schema.json  golden-unit-pairs.json
 */

export * from './units';
export * from './ids';
export * from './geometry';
export * from './model';
export * from './ops';
export * from './validate';
export * from './rooms';
export * from './fold';
export * from './sha256';

/**
 * Deterministic fixtures (fixed ids, the two-room demo plan). Test support that
 * `apps/web`'s store tests reuse — never import this from a runtime path.
 */
export * from './testing';
