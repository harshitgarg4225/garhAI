# `apps/web/public/fonts/` — the canvas label font

## The one file that has to be here

    inter-medium.woff

`features/canvas/overlays/render/overlayMaterials.ts` exports

```ts
export const LABEL_FONT_URL = '/fonts/inter-medium.woff';
```

and every troika `Text` on the canvas — dimension values, room names, room
areas, compliance markers — is created with it.

## Why an empty folder is a shipping blocker, not a nit

troika-three-text does not fail when a font URL 404s. It **falls back to
fetching Roboto from `fonts.gstatic.com`**. That means a missing file here
costs three things, none of which show up as a broken build:

1. **A §13 CSP violation.** The security checklist allows no third-party
   origins; the app would silently start making cross-origin font requests from
   the drawing surface.
2. **An offline failure.** `docker compose up` is the supported way to run this
   (§1) and the stack is meant to work with no internet and no API keys. Without
   this file, every dimension on the plan disappears on a machine with no route
   to Google.
3. **A drawing that is not the drawing.** Dimension text metrics come from the
   font. A fallback face has different advance widths, so the label boxes the
   placer packed (`tags/placement.ts`) no longer match the glyphs drawn in them,
   and labels start overlapping at exactly the zoom levels the placer was tuned
   for.

Dev is not blocked by this — the canvas renders, the tools work, the ops are
correct. Shipping is.

## Getting the file

Inter is SIL Open Font License 1.1, which is on the allowed list (§ licence
rules: Apache/MIT/BSD/MPL/OFL for assets). Take `Inter-Medium.woff` from the
official release and drop it here under the exact name above:

    https://github.com/rsms/inter/releases

Do **not** subset it in a way that drops the digits, the apostrophe or the
quotation mark: `12'-6"` is the primary thing this font renders, and a subset
built from Latin text alone will lose the foot and inch marks.

## Checking it worked

Open the Plan tab with the network panel filtered to `font`. You should see one
request, to your own origin, for `/fonts/inter-medium.woff` — and nothing at all
to `fonts.gstatic.com`.
