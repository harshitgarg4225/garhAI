import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import {
  ANNOTATION_ANCHOR_KINDS,
  DIRECTIONS_4,
  DIRECTIONS_8,
  OPENING_KINDS,
  OPENING_SWINGS,
  RAILING_KINDS,
  ROOM_TYPES,
  ROOM_TYPE_LABELS,
  SLAB_KINDS,
  SURFACE_GROUPS,
  VASTU_MODES,
  WALL_KINDS,
  STAIR_KINDS,
  FACADE_COMPONENT_KINDS,
} from './model';
import {
  OP_CATALOG,
  OP_TYPES,
  copilotOpSpecs,
  getOpSpec,
  isOp,
  isOpType,
  renderOpCatalogForPrompt,
} from './ops';
import { validateOpShape } from './validate';

function readSchema(name: string): Record<string, unknown> {
  const url = new URL(`../schema/${name}`, import.meta.url);
  return JSON.parse(readFileSync(url, 'utf8')) as Record<string, unknown>;
}

describe('OP_CATALOG covers the playbook §4 table exactly', () => {
  it('has 32 ops numbered 1..32 with no gaps', () => {
    expect(OP_CATALOG).toHaveLength(32);
    expect(OP_CATALOG.map((s) => s.number)).toEqual(
      Array.from({ length: 32 }, (_, i) => i + 1),
    );
  });

  it('has a unique type per entry, and OP_TYPES matches', () => {
    const types = OP_CATALOG.map((s) => s.type);
    expect(new Set(types).size).toBe(32);
    expect([...OP_TYPES]).toEqual(types);
  });

  it('exposes every op through getOpSpec / isOpType', () => {
    for (const type of OP_TYPES) {
      expect(isOpType(type)).toBe(true);
      expect(getOpSpec(type)?.type).toBe(type);
    }
    expect(isOpType('wall.teleport')).toBe(false);
    expect(getOpSpec('wall.teleport')).toBeUndefined();
  });

  it('describes every payload field with a unit where one applies', () => {
    for (const spec of OP_CATALOG) {
      expect(spec.title.length).toBeGreaterThan(0);
      expect(spec.summary.length).toBeGreaterThan(0);
      const names = spec.payload.map((f) => f.name);
      expect(new Set(names).size).toBe(names.length);
      for (const field of spec.payload) {
        expect(field.description.length).toBeGreaterThan(0);
        if (field.type === 'int-mm') expect(field.units).toBe('mm');
        if (field.type === 'int-mm2') expect(field.units).toBe('mm2');
        if (field.type === 'int-deg') expect(field.units).toBe('deg');
        if (field.type === 'enum') expect(field.enumValues?.length ?? 0).toBeGreaterThan(0);
        if (field.type === 'id') expect(field.idType).toBeDefined();
      }
    }
  });

  it('names the action values for every combined action-field op', () => {
    const combined = ['column.set', 'furniture.set', 'balcony.set', 'annotation.set'];
    for (const type of combined) {
      const spec = getOpSpec(type);
      expect(spec?.actions?.length).toBe(3);
      expect(spec?.payload.some((f) => f.name === 'action')).toBe(true);
    }
    for (const spec of OP_CATALOG) {
      if (!combined.includes(spec.type)) expect(spec.actions).toBeNull();
    }
  });

  it('every example is a structurally valid op', () => {
    for (const spec of OP_CATALOG) {
      expect(spec.example.type).toBe(spec.type);
      expect(isOp(spec.example)).toBe(true);
      const issues = validateOpShape(spec.example);
      expect(issues, `${spec.type}: ${JSON.stringify(issues)}`).toEqual([]);
    }
  });

  it('keeps plot/reg-profile, solver expansion and annotations out of copilot reach', () => {
    const copilot = copilotOpSpecs().map((s) => s.type);
    expect(copilot).not.toContain('solver.apply_option');
    expect(copilot).not.toContain('plot.set_boundary');
    expect(copilot).not.toContain('plot.set_reg_profile');
    expect(copilot).not.toContain('annotation.set');
    // ...but the bulk of the taxonomy IS available to it
    expect(copilot.length).toBeGreaterThanOrEqual(25);
  });
});

describe('renderOpCatalogForPrompt (§10 generates the system prompt from data)', () => {
  const prompt = renderOpCatalogForPrompt();

  it('states the units rule up front', () => {
    expect(prompt).toContain('INTEGER MILLIMETRES');
  });

  it('lists every copilot op with its fields and an example', () => {
    for (const spec of copilotOpSpecs()) {
      expect(prompt).toContain(`## ${spec.type}`);
      expect(prompt).toContain(spec.summary);
      for (const field of spec.payload) expect(prompt).toContain(`- ${field.name} (`);
      expect(prompt).toContain(JSON.stringify(spec.example));
    }
  });

  it('can include the non-copilot ops on request', () => {
    const all = renderOpCatalogForPrompt({ copilotOnly: false });
    expect(all).toContain('## solver.apply_option');
    expect(prompt).not.toContain('## solver.apply_option');
  });
});

describe('ops.schema.json is in lockstep with OP_CATALOG', () => {
  const schema = readSchema('ops.schema.json');
  const defs = schema.$defs as Record<string, { properties: { type: { const: string } } }>;

  it('has exactly one $def per op type, keyed by the type string', () => {
    const keys = Object.keys(defs).filter((k) => k !== 'OpMeta');
    expect(keys.sort()).toEqual([...OP_TYPES].sort());
    for (const key of keys) {
      expect(defs[key]!.properties.type.const).toBe(key);
    }
  });

  it('lists every op in the root oneOf', () => {
    const oneOf = schema.oneOf as { $ref: string }[];
    expect(oneOf.map((o) => o.$ref)).toEqual(OP_TYPES.map((t) => `#/$defs/${t}`));
  });

  it('requires every required catalogue field in the schema payload', () => {
    for (const spec of OP_CATALOG) {
      const payload = (
        defs[spec.type] as unknown as {
          properties: { payload: { required?: string[]; properties: Record<string, unknown> } };
        }
      ).properties.payload;
      const schemaProps = Object.keys(payload.properties);
      for (const field of spec.payload) {
        expect(schemaProps, `${spec.type}.${field.name}`).toContain(field.name);
      }
      // Conditionally-required fields (combined action ops) live in `allOf/if`,
      // so only check the unconditional ones.
      const required = payload.required ?? [];
      for (const name of required) {
        expect(spec.payload.map((f) => f.name)).toContain(name);
      }
    }
  });
});

describe('common.schema.json enums are in lockstep with the TS constants', () => {
  const common = readSchema('common.schema.json');
  const defs = common.$defs as Record<string, { enum?: string[] }>;

  const cases: [string, readonly string[]][] = [
    ['RoomType', ROOM_TYPES],
    ['WallKind', WALL_KINDS],
    ['OpeningKind', OPENING_KINDS],
    ['OpeningSwing', OPENING_SWINGS],
    ['StairKind', STAIR_KINDS],
    ['Direction4', DIRECTIONS_4],
    ['Direction8', DIRECTIONS_8],
    ['SlabKind', SLAB_KINDS],
    ['RailingKind', RAILING_KINDS],
    ['FacadeComponentKind', FACADE_COMPONENT_KINDS],
    ['SurfaceGroup', SURFACE_GROUPS],
    ['VastuMode', VASTU_MODES],
    ['AnnotationAnchorKind', ANNOTATION_ANCHOR_KINDS],
  ];

  it.each(cases)('%s', (name, values) => {
    expect(defs[name]?.enum).toEqual([...values]);
  });

  it('labels every room type for the UI', () => {
    for (const type of ROOM_TYPES) {
      expect(ROOM_TYPE_LABELS[type].length).toBeGreaterThan(0);
    }
  });
});

describe('validation-issue.schema.json is in lockstep with the codes', () => {
  it('lists exactly the VALIDATION_CODES', async () => {
    const { VALIDATION_CODES } = await import('./validate');
    const schema = readSchema('validation-issue.schema.json');
    const defs = schema.$defs as Record<string, { enum?: string[] }>;
    expect(defs.ValidationCode?.enum).toEqual([...VALIDATION_CODES]);
  });
});
