/**
 * baseTool.ts — the three §12 guarantees, implemented once.
 *
 * Esc cancels, Enter commits, and typing a number overrides the mouse. Those
 * are stated as hard requirements for EVERY tool, which means they must not be
 * seven separate implementations that drift. {@link BaseTool} owns them:
 *
 *   - the numeric-entry buffer and its key routing (`numericEntry.ts`),
 *   - the escape ladder (a mistyped number must not throw away the drawing),
 *   - the preview envelope and its version counter,
 *   - the "does this key belong to me?" answer the controller needs before the
 *     keyboard map sees the event.
 *
 * A concrete tool implements the pointer verbs, `buildPreview`, `commit`, and
 * `reset` — the parts that are actually different.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE ESCAPE LADDER
 * ────────────────────────────────────────────────────────────────────────────
 *   Esc with a numeric buffer   → clear the buffer, keep drawing
 *   Esc while drawing           → cancel the whole thing, emit nothing
 *   Esc while idle              → decline the key, so a dialog can close
 *
 * The first rung is not a nicety. A ten-segment wall chain thrown away because
 * someone typed `36OO` is the kind of thing that stops people trusting the
 * keyboard, and once they stop, every §12 keyboard requirement is decoration.
 *
 * NO REACT. Nothing in this file imports React, and nothing in it allocates per
 * pointer move beyond the preview object it returns — §14 budgets the pointer
 * path at a fraction of 16 ms and a tool is only one of the things spending it.
 */

import type { Pt } from '@garh/model';

import {
  clearEntry,
  createEntry,
  entryValueFor,
  entryView,
  feedKey,
  isEntryActive,
  resetBuffer,
  wantsKey as entryWantsKey,
  type NumericEntryState,
  type NumericField,
} from './numericEntry';
import {
  TOOL_RESPONSE_NONE,
  handled,
  type PreviewShape,
  type Readout,
  type SnapView,
  type Tool,
  type ToolBlock,
  type ToolChip,
  type ToolCommit,
  type ToolContext,
  type ToolId,
  type ToolKeyInput,
  type ToolPhase,
  type ToolPointerInput,
  type ToolPreview,
  type ToolResponse,
  type ToolSettings,
} from './types';

/** The pieces a concrete tool supplies; the envelope is the base's job. */
export interface PreviewParts {
  readonly shape: PreviewShape;
  readonly snap?: SnapView | null | undefined;
  readonly readouts?: readonly Readout[] | undefined;
  readonly chips?: readonly ToolChip[] | undefined;
  readonly blocked?: ToolBlock | null | undefined;
  readonly cursorMm?: Pt | null | undefined;
  readonly hint: string;
}

export abstract class BaseTool implements Tool {
  abstract readonly id: ToolId;

  /** Mutable; the verbs move it. Exposed read-only through `phase`. */
  protected phaseState: ToolPhase = 'idle';

  protected entry: NumericEntryState;

  /** Bumped by {@link touch}; consumers diff on it instead of deep-comparing. */
  private previewVersion = 0;

  constructor(fields: readonly NumericField[] = []) {
    this.entry = createEntry(fields);
  }

  get phase(): ToolPhase {
    return this.phaseState;
  }

  // ── pointer verbs: inert by default ──────────────────────────────────────

  onPointerDown(_ctx: ToolContext, _event: ToolPointerInput): ToolResponse {
    return TOOL_RESPONSE_NONE;
  }

  onPointerMove(_ctx: ToolContext, _event: ToolPointerInput): ToolResponse {
    return TOOL_RESPONSE_NONE;
  }

  onPointerUp(_ctx: ToolContext, _event: ToolPointerInput): ToolResponse {
    return TOOL_RESPONSE_NONE;
  }

  // ── keys ─────────────────────────────────────────────────────────────────

  /**
   * The §12 key contract. Order matters and is the ladder in the header:
   * numeric entry first (it is the only thing that can be "in the middle of"
   * something), then the universal verbs, then the tool's own modifiers.
   */
  onKey(ctx: ToolContext, event: ToolKeyInput): ToolResponse {
    if (event.key === 'Escape') {
      const cleared = clearEntry(this.entry);
      if (cleared !== null) {
        this.entry = cleared;
        this.onEntryChanged(ctx);
        return handled();
      }
      if (this.phaseState === 'idle') return TOOL_RESPONSE_NONE;
      this.cancel();
      return handled();
    }

    if (event.key === 'Enter') {
      // A tool may claim Enter for itself — the wall tool turns "type 3600,
      // press Enter" into "place that segment and keep going", which is what
      // every CAD line command does and what the numeric entry is for.
      const intercepted = this.onEnterKey(ctx);
      if (intercepted !== null) return intercepted;
      const commit = this.commit(ctx);
      if (commit === null) return TOOL_RESPONSE_NONE;
      this.afterCommit(ctx);
      return handled({ commit, settingsPatch: this.drainSettings() });
    }

    if (entryWantsKey(this.entry, event) && this.phaseState !== 'idle') {
      const step = feedKey(this.entry, event);
      if (step.action === 'ignored') return TOOL_RESPONSE_NONE;
      this.entry = step.state;
      this.onEntryChanged(ctx);
      return handled();
    }

    return this.onToolKey(ctx, event);
  }

  /**
   * Per-tool modifier keys (`X` flips a swing, `[`/`]` change a stair type).
   * Called only for keys the numeric entry and the universal verbs did not want.
   */
  protected onToolKey(_ctx: ToolContext, _event: ToolKeyInput): ToolResponse {
    return TOOL_RESPONSE_NONE;
  }

  /**
   * Claim Enter. Return null to get the default behaviour (commit and reset).
   */
  protected onEnterKey(_ctx: ToolContext): ToolResponse | null {
    return null;
  }

  /**
   * Called after the buffer changed. Tools override to recompute the geometry
   * a typed value implies — the wall tool's "3600 means the segment ends 3600
   * along the direction the mouse chose".
   */
  protected onEntryChanged(_ctx: ToolContext): void {
    this.touch();
  }

  /**
   * Does this key belong to the tool rather than to the keyboard map?
   *
   * True while mid-draw for digits and length glyphs (§12: typing a number
   * overrides the mouse), plus Backspace and Delete, which the browser would
   * otherwise turn into navigation. Overridden by the select tool, which wants
   * Delete while idle.
   */
  wantsKey(event: ToolKeyInput): boolean {
    if (this.phaseState === 'idle') return false;
    if (event.key === 'Backspace' || event.key === 'Delete') return true;
    if (event.key === 'Escape' || event.key === 'Enter') return true;
    return entryWantsKey(this.entry, event);
  }

  // ── numeric entry helpers for subclasses ─────────────────────────────────

  /** The applicable typed value for `fieldId`, or null. */
  protected typed(fieldId: string): number | null {
    return entryValueFor(this.entry, fieldId);
  }

  protected entryIsActive(): boolean {
    return isEntryActive(this.entry);
  }

  /** Clear the buffer after applying it, so the next segment starts fresh. */
  protected consumeEntry(): void {
    this.entry = resetBuffer(this.entry);
  }

  // ── settings the tool wants changed ──────────────────────────────────────

  /**
   * A settings change the tool decided on while committing — a typed width
   * becoming the default, say. Drained into the {@link ToolResponse} so the
   * controller applies it; a tool never writes the settings store itself.
   */
  protected pendingSettings: Partial<ToolSettings> | null = null;

  protected drainSettings(): Partial<ToolSettings> | undefined {
    const patch = this.pendingSettings;
    this.pendingSettings = null;
    return patch ?? undefined;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  /** Mark the preview dirty. Cheap; call it whenever anything visible changed. */
  protected touch(): void {
    this.previewVersion += 1;
  }

  preview(ctx: ToolContext): ToolPreview {
    const parts = this.buildPreview(ctx);
    return {
      toolId: this.id,
      phase: this.phaseState,
      shape: parts.shape,
      snap: parts.snap ?? null,
      readouts: parts.readouts ?? [],
      entry: entryView(this.entry, ctx.unitsDisplay),
      chips: parts.chips ?? [],
      blocked: parts.blocked ?? null,
      cursorMm: parts.cursorMm ?? null,
      hint: parts.hint,
      version: this.previewVersion,
    };
  }

  protected abstract buildPreview(ctx: ToolContext): PreviewParts;

  // ── commit / cancel ──────────────────────────────────────────────────────

  abstract commit(ctx: ToolContext): ToolCommit | null;

  /**
   * Hook for what happens to the tool AFTER its ops were handed over. The
   * default is a full reset; the wall tool overrides it to keep drawing.
   */
  protected afterCommit(_ctx: ToolContext): void {
    this.cancel();
  }

  cancel(): void {
    this.phaseState = 'idle';
    this.entry = resetBuffer(this.entry);
    this.reset();
    this.touch();
  }

  /** Clear the tool's own state. Called by {@link cancel}. */
  protected abstract reset(): void;
}
