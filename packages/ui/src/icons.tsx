/**
 * icons.tsx — the icon set, as inline SVG path data.
 *
 * No icon-font, no icon package: §14 budgets the initial bundle at <1.5MB gz
 * and an icon library is the classic way to spend 40kB on twelve glyphs. These
 * are hand-drawn on a 24×24 grid, stroked with `currentColor` so they inherit
 * text colour in both themes automatically.
 *
 * TRADEMARK NOTE: there is deliberately no WhatsApp logo here. §15 asks for a
 * "Share on WhatsApp" action, which we render as a neutral chat-bubble icon
 * plus the words. Shipping the WhatsApp glyph would put a third-party
 * trademarked mark in the bundle; the deep link works exactly the same without
 * it, and the label carries the meaning.
 */

import type { SVGProps } from 'react';
import { cn } from './cn';

/** Stroked glyphs (the default). */
const STROKE_PATHS = {
  plus: 'M12 5 L12 19 M5 12 L19 12',
  minus: 'M5 12 L19 12',
  check: 'M4.5 12.5 L9.5 17.5 L19.5 6.5',
  x: 'M6 6 L18 18 M18 6 L6 18',
  'chevron-down': 'M6 9 L12 15 L18 9',
  'chevron-up': 'M6 15 L12 9 L18 15',
  'chevron-right': 'M9 6 L15 12 L9 18',
  'chevron-left': 'M15 6 L9 12 L15 18',
  'arrow-right': 'M4.5 12 L19.5 12 M14 6.5 L19.5 12 L14 17.5',
  'arrow-left': 'M19.5 12 L4.5 12 M10 6.5 L4.5 12 L10 17.5',
  'alert-triangle': 'M12 3.6 L2.7 19.5 L21.3 19.5 Z M12 9.4 L12 14 M12 17.1 L12.01 17.1',
  'alert-circle':
    'M12 3.75 A8.25 8.25 0 1 0 12 20.25 A8.25 8.25 0 0 0 12 3.75 Z M12 7.8 L12 13 M12 16.3 L12.01 16.3',
  'check-circle':
    'M12 3.75 A8.25 8.25 0 1 0 12 20.25 A8.25 8.25 0 0 0 12 3.75 Z M8.2 12.2 L10.8 14.8 L15.8 9.4',
  info: 'M12 3.75 A8.25 8.25 0 1 0 12 20.25 A8.25 8.25 0 0 0 12 3.75 Z M12 11 L12 16.2 M12 7.9 L12.01 7.9',
  clock:
    'M12 3.75 A8.25 8.25 0 1 0 12 20.25 A8.25 8.25 0 0 0 12 3.75 Z M12 7.5 L12 12.4 L15.4 14.4',
  search: 'M11 4.5 A6.5 6.5 0 1 0 11 17.5 A6.5 6.5 0 0 0 11 4.5 Z M15.7 15.7 L20 20',
  share:
    'M6.5 9.5 A2.5 2.5 0 1 0 6.5 14.5 A2.5 2.5 0 0 0 6.5 9.5 Z M17.5 3.5 A2.5 2.5 0 1 0 17.5 8.5 A2.5 2.5 0 0 0 17.5 3.5 Z M17.5 15.5 A2.5 2.5 0 1 0 17.5 20.5 A2.5 2.5 0 0 0 17.5 15.5 Z M8.7 10.7 L15.3 7.3 M8.7 13.3 L15.3 16.7',
  sparkles:
    'M11 3.5 L12.6 8.9 L18 10.5 L12.6 12.1 L11 17.5 L9.4 12.1 L4 10.5 L9.4 8.9 Z M18 15 L18.7 17.3 L21 18 L18.7 18.7 L18 21 L17.3 18.7 L15 18 L17.3 17.3 Z',
  undo: 'M4 11.5 L8.5 7 M4 11.5 L8.5 16 M4 11.5 L14 11.5 A4.5 4.5 0 0 1 14 20.5 L11.5 20.5',
  redo: 'M20 11.5 L15.5 7 M20 11.5 L15.5 16 M20 11.5 L10 11.5 A4.5 4.5 0 0 0 10 20.5 L12.5 20.5',
  cursor: 'M6 3.2 L6 18.6 L10.1 14.6 L12.7 20.6 L15.3 19.4 L12.8 13.6 L18.2 13.6 Z',
  wall: 'M3 7 L21 7 L21 17 L3 17 Z M3 12 L21 12 M9 7 L9 12 M15 12 L15 17',
  door: 'M6.5 20.5 L6.5 5 L15.5 3 L15.5 21 Z M12.8 12.4 L12.81 12.4 M17.5 20.5 L20.5 20.5',
  window: 'M4 5 L20 5 L20 19 L4 19 Z M4 12 L20 12 M12 5 L12 19',
  stair: 'M3.5 20 L3.5 16 L8 16 L8 12.5 L12.5 12.5 L12.5 9 L17 9 L17 5.5 L21 5.5',
  balcony: 'M3 9 L21 9 M3 19 L21 19 M4.5 9 L4.5 19 M9.5 9 L9.5 19 M14.5 9 L14.5 19 M19.5 9 L19.5 19',
  ruler:
    'M2.5 14 L10 21.5 L21.5 10.5 L14 3 Z M6.5 12.5 L8.5 14.5 M10 9 L12 11 M13.5 5.5 L15.5 7.5',
  sofa: 'M3 13 L3 10 A2.5 2.5 0 0 1 8 10 L8 13 M16 13 L16 10 A2.5 2.5 0 0 1 21 10 L21 13 M3 13 L21 13 L21 17.5 L3 17.5 Z M5.5 17.5 L5.5 19.5 M18.5 17.5 L18.5 19.5',
  cube: 'M12 3 L20.5 7.5 L20.5 16.5 L12 21 L3.5 16.5 L3.5 7.5 Z M12 12 L20.5 7.5 M12 12 L3.5 7.5 M12 12 L12 21',
  image:
    'M3.5 5 L20.5 5 L20.5 19 L3.5 19 Z M3.5 15.5 L8.5 11 L13 15 L16 12.5 L20.5 16.5 M15.6 9.2 L15.61 9.2',
  sheet: 'M6 3 L14 3 L18.5 7.5 L18.5 21 L6 21 Z M14 3 L14 7.5 L18.5 7.5 M9 12.5 L15.5 12.5 M9 16.5 L15.5 16.5',
  shield: 'M12 3 L20 6 L20 12 C20 16.5 16.5 19.8 12 21.3 C7.5 19.8 4 16.5 4 12 L4 6 Z',
  'shield-check':
    'M12 3 L20 6 L20 12 C20 16.5 16.5 19.8 12 21.3 C7.5 19.8 4 16.5 4 12 L4 6 Z M8.8 12.2 L11.2 14.6 L15.6 9.9',
  home: 'M3.5 11 L12 3.5 L20.5 11 M6 9.5 L6 20 L18 20 L18 9.5 M10 20 L10 14 L14 14 L14 20',
  'log-out': 'M15 7.5 L15 4.5 L4.5 4.5 L4.5 19.5 L15 19.5 L15 16.5 M9.5 12 L20 12 M16.5 8.5 L20 12 L16.5 15.5',
  layers: 'M12 3 L21 7.5 L12 12 L3 7.5 Z M3 12 L12 16.5 L21 12 M3 16.5 L12 21 L21 16.5',
  compass:
    'M12 3.5 A8.5 8.5 0 1 0 12 20.5 A8.5 8.5 0 0 0 12 3.5 Z M15.5 8.5 L13.3 13.3 L8.5 15.5 L10.7 10.7 Z',
  trash:
    'M4.5 6.5 L19.5 6.5 M9.5 6.5 L9.5 4.2 L14.5 4.2 L14.5 6.5 M6.5 6.5 L7.5 20 L16.5 20 L17.5 6.5 M10.3 10 L10.7 16.5 M13.7 10 L13.3 16.5',
  edit: 'M4 20 L4 16 L16 4 L20 8 L8 20 Z M13.5 6.5 L17.5 10.5',
  'external-link': 'M14 4.5 L19.5 4.5 L19.5 10 M19.5 4.5 L11.5 12.5 M17 14 L17 19.5 L4.5 19.5 L4.5 7 L10 7',
  copy: 'M8 8 L8 4.5 L19.5 4.5 L19.5 16 L16 16 M4.5 8 L16 8 L16 19.5 L4.5 19.5 Z',
  download: 'M12 3.5 L12 15 M7.5 10.5 L12 15 L16.5 10.5 M4.5 19.5 L19.5 19.5',
  refresh:
    'M20 5.5 L20 10.5 L15 10.5 M4 18.5 L4 13.5 L9 13.5 M19.2 13.6 A7.5 7.5 0 0 1 5.5 16.3 M4.8 10.4 A7.5 7.5 0 0 1 18.5 7.7',
  grid: 'M4 9 L20 9 M4 15 L20 15 M9 4 L9 20 M15 4 L15 20',
  folder: 'M3.5 6.5 L9.3 6.5 L11.3 9 L20.5 9 L20.5 19 L3.5 19 Z',
  mail: 'M3.5 6 L20.5 6 L20.5 18 L3.5 18 Z M3.5 6.5 L12 13 L20.5 6.5',
  phone: 'M7 3.5 L17 3.5 L17 20.5 L7 20.5 Z M10.5 17.9 L13.5 17.9',
  lock: 'M7.5 10.5 L7.5 8 A4.5 4.5 0 0 1 16.5 8 L16.5 10.5 M5.5 10.5 L18.5 10.5 L18.5 20 L5.5 20 Z',
  message: 'M12 4 C6.8 4 3 7.5 3 11.8 C3 14 4 15.9 5.7 17.3 L4.8 20.6 L8.6 19 C9.6 19.4 10.8 19.6 12 19.6 C17.2 19.6 21 16.1 21 11.8 C21 7.5 17.2 4 12 4 Z',
  lightbulb: 'M9.3 18 L14.7 18 M10.3 21 L13.7 21 M12 3 A6 6 0 0 0 9.1 14.3 L9.3 18 M12 3 A6 6 0 0 1 14.9 14.3 L14.7 18',
  user: 'M12 4 A3.8 3.8 0 1 0 12 11.6 A3.8 3.8 0 0 0 12 4 Z M4.5 20.5 C4.5 16.6 7.9 14 12 14 C16.1 14 19.5 16.6 19.5 20.5',
  users: 'M9.5 4.5 A3.5 3.5 0 1 0 9.5 11.5 A3.5 3.5 0 0 0 9.5 4.5 Z M2.5 20 C2.5 16.4 5.6 14 9.5 14 C13.4 14 16.5 16.4 16.5 20 M16.2 5.2 A3.4 3.4 0 0 1 16.2 11.3 M18 14.4 C20.2 15.2 21.5 17.2 21.5 20',
  'more-horizontal': 'M6 12 L6.01 12 M12 12 L12.01 12 M18 12 L18.01 12',
  loader: 'M12 3.5 L12 7 M12 17 L12 20.5 M3.5 12 L7 12 M17 12 L20.5 12 M6 6 L8.5 8.5 M15.5 15.5 L18 18 M18 6 L15.5 8.5 M8.5 15.5 L6 18',
  play: 'M7 4.5 L19 12 L7 19.5 Z',
  pause: 'M9 5 L9 19 M15 5 L15 19',
  pin: 'M12 21 L12 13.5 M12 3 A4.5 4.5 0 0 0 12 13.5 A4.5 4.5 0 0 0 12 3 Z',
  filter: 'M3.5 5.5 L20.5 5.5 L14 13 L14 19.5 L10 21 L10 13 Z',
  'panel-right': 'M3.5 5 L20.5 5 L20.5 19 L3.5 19 Z M15 5 L15 19',
  'panel-left': 'M3.5 5 L20.5 5 L20.5 19 L3.5 19 Z M9 5 L9 19',
} as const;

export type IconName = keyof typeof STROKE_PATHS;

/** Names, exported so a storybook/test can assert every glyph renders. */
export const ICON_NAMES = Object.keys(STROKE_PATHS) as IconName[];

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name' | 'children'> {
  name: IconName;
  /** Pixel size of the square. Default 16 — the size used in buttons and chips. */
  size?: number | undefined;
  /**
   * Accessible name. Omit for purely decorative icons that sit next to a text
   * label (the default) — they are then `aria-hidden` so screen readers do not
   * announce "image" before every button.
   */
  title?: string | undefined;
  className?: string | undefined;
}

export function Icon({ name, size = 16, title, className, ...rest }: IconProps): JSX.Element {
  const decorative = title === undefined;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('shrink-0', className)}
      aria-hidden={decorative ? true : undefined}
      role={decorative ? undefined : 'img'}
      focusable="false"
      {...rest}
    >
      {title === undefined ? null : <title>{title}</title>}
      <path d={STROKE_PATHS[name]} />
    </svg>
  );
}

/**
 * The one spinner in the product. §15 forbids a *bare* spinner as a loading
 * state — use `Skeleton` for that. This exists for in-button busy state and
 * for job cards, where the surrounding text already says what is happening.
 */
export function Spinner({
  size = 16,
  className,
  label,
}: {
  size?: number | undefined;
  className?: string | undefined;
  label?: string | undefined;
}): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={cn('animate-spin shrink-0', className)}
      aria-hidden={label === undefined ? true : undefined}
      role={label === undefined ? undefined : 'img'}
      focusable="false"
    >
      {label === undefined ? null : <title>{label}</title>}
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" opacity="0.2" fill="none" />
      <path
        d="M21 12 A9 9 0 0 0 12 3"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
