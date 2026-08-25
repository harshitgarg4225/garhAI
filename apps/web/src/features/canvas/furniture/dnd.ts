/**
 * The drag-and-drop contract between the browser panel and the canvas.
 *
 * Its own module on purpose: the canvas core has to read a drop payload, and it
 * should not have to import a React panel component to do it.
 *
 * A private MIME type rather than `text/plain` alone, so the canvas can tell a
 * furniture drag from a file drop, a DXF, or text dragged in from another
 * window — and can decline the ones that are not ours instead of guessing.
 */

/** The private type. Nothing outside this feature should write it. */
export const FURNITURE_DND_MIME = 'application/x-garh-furniture';

/** Put a catalogue item on a drag. Called from the browser row's `dragstart`. */
export function setFurnitureDragPayload(dataTransfer: DataTransfer, catalogId: string): void {
  dataTransfer.setData(FURNITURE_DND_MIME, catalogId);
  // Plain-text fallback: dropping onto a text field then does something sane
  // instead of nothing.
  dataTransfer.setData('text/plain', catalogId);
  dataTransfer.effectAllowed = 'copy';
}

/** Read a drop. Returns null when the drag did not come from this feature. */
export function readFurnitureDragPayload(dataTransfer: DataTransfer | null): string | null {
  if (dataTransfer === null) return null;
  const id = dataTransfer.getData(FURNITURE_DND_MIME);
  return id === '' ? null : id;
}

/**
 * True when a `dragover` should be accepted.
 *
 * The canvas must call `preventDefault()` on `dragover` or the browser refuses
 * the drop entirely — and `getData` returns `''` during `dragover` for security
 * reasons, so the decision has to be made from `types` instead.
 */
export function isFurnitureDrag(dataTransfer: DataTransfer | null): boolean {
  if (dataTransfer === null) return false;
  return Array.from(dataTransfer.types).includes(FURNITURE_DND_MIME);
}
