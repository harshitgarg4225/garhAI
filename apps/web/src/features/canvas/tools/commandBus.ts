/**
 * commandBus.ts — how chrome asks the ACTIVE TOOL to do something.
 *
 * The options bar is React; the tool is a plain object in a ref inside
 * `useToolController`. The bar cannot reach the tool, and it must not: a
 * React component holding a tool instance is a re-render per pointer move
 * waiting to happen (§14). So the bar SENDS a `ToolCommand` here and the
 * controller, which already owns the tool, forwards it as `tool.onCommand`.
 *
 * Same shape as `previewBus.ts`, going the other way. A command with no
 * active tool, or a tool that does not implement `onCommand`, is dropped —
 * the bar only shows a button when the select tool is armed, so that is a
 * race, not a bug, and the honest answer is nothing rather than a throw.
 */

import type { ToolCommand } from './types';

export type ToolCommandListener = (command: ToolCommand) => void;

export class ToolCommandBus {
  private readonly listeners = new Set<ToolCommandListener>();

  send(command: ToolCommand): void {
    for (const listener of this.listeners) listener(command);
  }

  subscribe(listener: ToolCommandListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
}

export const toolCommandBus = new ToolCommandBus();
