/**
 * Skeleton — the ONLY loading state in the product.
 *
 * §15: "skeletons everywhere (never blank, never spinner-only)". A skeleton
 * that mirrors the shape of the content it replaces makes a 700ms fetch feel
 * like a paint; a centred spinner makes the same 700ms feel like a stall.
 *
 * The shimmer is decoration and disappears under `prefers-reduced-motion`
 * (handled globally in tokens.css). Screen readers get a single polite
 * "Loading …" from `SkeletonRegion`, not one announcement per bar.
 */

import type { ReactNode } from 'react';
import { cn } from './cn';

export interface SkeletonProps {
  className?: string | undefined;
  /** Rounded pill (text) vs square (thumbnails). Default 'text'. */
  shape?: 'text' | 'block' | 'circle' | undefined;
}

const SHAPES = {
  text: 'rounded',
  block: 'rounded-md',
  circle: 'rounded-full',
} as const;

export function Skeleton({ className, shape = 'text' }: SkeletonProps): JSX.Element {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'relative block overflow-hidden bg-surface-muted',
        SHAPES[shape],
        // The shimmer sweep. `before:` keeps it to one DOM node.
        'before:absolute before:inset-0 before:-translate-x-full before:animate-shimmer',
        'before:bg-gradient-to-r before:from-transparent before:via-line before:to-transparent',
        className,
      )}
    />
  );
}

/**
 * Wraps a skeleton tree in a polite live region with one honest sentence.
 * Always say what is loading — "Loading your projects" beats "Loading".
 */
export function SkeletonRegion({
  label,
  className,
  children,
}: {
  label: string;
  className?: string | undefined;
  children: ReactNode;
}): JSX.Element {
  return (
    <div role="status" aria-live="polite" aria-busy="true" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

/** N lines of text, last one short — the shape real prose has. */
export function SkeletonText({
  lines = 3,
  className,
}: {
  lines?: number | undefined;
  className?: string | undefined;
}): JSX.Element {
  return (
    <span className={cn('flex flex-col gap-2', className)}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn('h-3', i === lines - 1 ? 'w-2/5' : 'w-full')} />
      ))}
    </span>
  );
}

/** The dashboard project-tile placeholder. Matches ProjectCard's geometry. */
export function SkeletonProjectCard(): JSX.Element {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <Skeleton className="mb-3 h-24 w-full" shape="block" />
      <Skeleton className="h-4 w-3/5" />
      <Skeleton className="mt-2 h-3 w-2/5" />
      <div className="mt-3 flex gap-1.5">
        <Skeleton className="h-5 w-16" shape="block" />
        <Skeleton className="h-5 w-16" shape="block" />
        <Skeleton className="h-5 w-16" shape="block" />
        <Skeleton className="h-5 w-16" shape="block" />
      </div>
    </div>
  );
}

/** Inspector / form placeholder: label + control, repeated. */
export function SkeletonForm({ rows = 4 }: { rows?: number }): JSX.Element {
  return (
    <div className="flex flex-col gap-4">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex flex-col gap-1.5">
          <Skeleton className="h-2.5 w-20" />
          <Skeleton className="h-9 w-full" shape="block" />
        </div>
      ))}
    </div>
  );
}

/** Full-bleed placeholder for a canvas / drawing area. */
export function SkeletonCanvas({ className }: { className?: string | undefined }): JSX.Element {
  return <Skeleton className={cn('h-full min-h-48 w-full', className)} shape="block" />;
}
