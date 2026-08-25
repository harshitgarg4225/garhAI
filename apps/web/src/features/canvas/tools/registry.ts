/**
 * registry.ts — tool id → tool instance, and the copy that names them.
 *
 * One place, because three things have to agree about what "the door tool" is:
 * the keyboard map (`lib/keymap.ts`, which owns the letters), the tool rail's
 * buttons (§15 accessibility: every keyboard command has a mouse equivalent),
 * and the controller that instantiates the machine. A registry is how they
 * agree without importing each other.
 *
 * A fresh instance is created on every activation rather than being pooled: a
 * tool's state IS its half-finished drawing, and reactivating the wall tool
 * must not resume a chain abandoned ten minutes ago.
 */

import { TOOL_IDS, TOOL_SHORTCUT, type ToolId } from '../../../lib/keymap';

import { BalconyTool } from './balconyTool';
import { FurnitureTool } from './furnitureTool';
import { MeasureTool } from './measureTool';
import { OpeningTool } from './openingTool';
import { SelectTool } from './selectTool';
import { StairTool } from './stairTool';
import type { Tool } from './types';
import { WallTool } from './wallTool';

export { TOOL_IDS, TOOL_SHORTCUT };

/** Display copy for the tool rail. §15 tone: plain, warm, no jargon. */
export interface ToolMeta {
  readonly id: ToolId;
  readonly label: string;
  /** One sentence for the tooltip. */
  readonly description: string;
  /** Keyboard label, from `lib/keymap` so the two cannot disagree. */
  readonly shortcut: string;
  /** True when the tool emits ops (the measure tool does not). */
  readonly mutates: boolean;
}

export const TOOL_META: Readonly<Record<ToolId, ToolMeta>> = {
  select: {
    id: 'select',
    label: 'Select',
    description: 'Select and move things.',
    shortcut: TOOL_SHORTCUT.select,
    mutates: true,
  },
  wall: {
    id: 'wall',
    label: 'Wall',
    description: 'Draw walls. Type a length while drawing to set it exactly.',
    shortcut: TOOL_SHORTCUT.wall,
    mutates: true,
  },
  door: {
    id: 'door',
    label: 'Door',
    description: 'Place a door on a wall.',
    shortcut: TOOL_SHORTCUT.door,
    mutates: true,
  },
  window: {
    id: 'window',
    label: 'Window',
    description: 'Place a window or a ventilator on a wall.',
    shortcut: TOOL_SHORTCUT.window,
    mutates: true,
  },
  stair: {
    id: 'stair',
    label: 'Stair',
    description: 'Place a staircase, worked out from the floor height.',
    shortcut: TOOL_SHORTCUT.stair,
    mutates: true,
  },
  balcony: {
    id: 'balcony',
    label: 'Balcony',
    description: 'Draw a balcony or projection.',
    shortcut: TOOL_SHORTCUT.balcony,
    mutates: true,
  },
  measure: {
    id: 'measure',
    label: 'Measure',
    description: 'Measure a distance. Changes nothing.',
    shortcut: TOOL_SHORTCUT.measure,
    mutates: false,
  },
  furniture: {
    id: 'furniture',
    label: 'Furniture',
    description: 'Place furniture from the catalogue.',
    shortcut: TOOL_SHORTCUT.furniture,
    mutates: true,
  },
};

/** Build the machine for a tool id. Always a new instance — see the header. */
export function createTool(id: ToolId): Tool {
  switch (id) {
    case 'wall':
      return new WallTool();
    case 'door':
      return new OpeningTool('door');
    case 'window':
      return new OpeningTool('window');
    case 'stair':
      return new StairTool();
    case 'balcony':
      return new BalconyTool();
    case 'measure':
      return new MeasureTool();
    case 'furniture':
      return new FurnitureTool();
    case 'select':
    default:
      return new SelectTool();
  }
}
