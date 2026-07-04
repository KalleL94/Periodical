# Mobile schedule matrix: frozen date column (Phase 1)

Date: 2026-06-30
Branch: `fix/mobile-schedule-matrix`

## Problem

The team schedule matrices (`month_all.html`, `year_all.html`) render days as rows
and people as columns. On a phone the matrix is far wider than the viewport, so it
is unreadable. Two concrete defects:

1. A global responsive rule in `tables.css` (`@media (max-width: 800px)`) turns
   *every* table into a card-stacked layout (`thead` hidden, each `td` becomes a
   labelled block). The matrix is built for horizontal scroll, so this rule
   collapses it into a broken hybrid. Only `.breakdown-table` is currently exempt.
2. The leading day/date columns never freeze on mobile, so when you scroll sideways
   you lose the row's identity (which day a shift belongs to).

## Decision

Keep the matrix intact and make the leading columns freeze on horizontal scroll
(user-selected direction: "frozen date column + scroll"). This respects the product
principle that colour belongs to the data: we touch navigation, not the shift pills.

## Approach (CSS-only)

All changes are scoped to `.month-schedule`, so both matrix templates are covered by
one rule despite using different width utility classes (`w-sm`/`w-md` in month_all,
`w-md`/`w-lg` in year_all).

1. **Exempt the matrix from the card rule** at `max-width: 800px`: restore
   `display: table-header-group` / `table-cell` / `table-row` for `.month-schedule`,
   the same way `.breakdown-table` is already exempted.
2. **Freeze the two leading columns** (weekday + date) with `position: sticky`:
   - Column 1 (`nth-child(1)`): `left: 0`, an explicit deterministic width
     (`--frozen-col1: 3.75rem`), reduced font so the longest weekday ("Torsdag")
     fits without wrapping. The explicit width is required so column 2's offset is
     exact regardless of which width class the template used.
   - Column 2 (`nth-child(2)`): `left: var(--frozen-col1)`, with a strong right
     border (`--line-strong`) marking the boundary between frozen and scrolling
     area, which also signals "more content to the right".
   - Header corner cells get the highest sticky z so they win at the top-left
     intersection of vertical (thead) and horizontal (column) sticky.
3. **Keep body-level horizontal scroll** (the existing `.month-grid` viewport
   breakout). This is a deliberate existing choice that preserves the vertical
   sticky `thead`; wrapping the table in an `overflow-x` container would clip it.

## Latent bug fixed along the way

`--z-sticky`, `--z-modal`, `--z-tooltip` are referenced across `tables.css` and
`navigation.css` but never defined in `:root`, so they resolve to `z-index: auto`.
Define them (sticky < modal < tooltip) so sticky stacking is correct and the new
frozen columns stack above the scrolling shift cells. App-wide improvement, no visual
change to existing working surfaces.

## Out of scope (Phase 2)

`day.html` and `dashboard.html` polish. Specced separately after Phase 1 is verified
in the browser.

## Verification

Render `/month` (team) and `/year` (team) at 375px width via the browse tool.
Confirm: matrix is a scrollable table (not cards); weekday + date columns stay pinned
while shift columns scroll under them; sticky `thead` still pins on vertical scroll;
the "today" row still reads across; desktop layout is unchanged at >800px.

## Final implementation (shipped, supersedes point 3 above)

Point 3 ("keep body-level horizontal scroll") was wrong and was caught in testing.
On real phones and in Firefox responsive mode, when content is wider than the viewport
with `width=device-width`, the browser expands the LAYOUT viewport to the content width
and lets you pan the visual viewport instead of scrolling the document. `position:
sticky; left:0` then pins to the offscreen layout-viewport edge, so the frozen columns
drift away. Plain headless Chromium document-scrolls, so this was invisible until
reproduced with CDP mobile emulation (`Emulation.setDeviceMetricsOverride ... mobile:true`).

Shipped fix (`layout.css`, `@media max-width:800px`): the matrix scrolls inside its own
container. `.month-grid { display:block; overflow:auto; max-height:80vh }` and
`.month-schedule { width:max-content; min-width:0 }`. Sticky then pins to the container,
which stays put; `max-height` makes it a pane so the sticky `thead` (top) also pins here.
Desktop keeps the flex/full-bleed model.

Also shipped:
- Frozen-column `z-index` uses LITERAL values in `tables.css` (2 body, 3 scrolling
  header, 4 frozen corner), not `var(--z-sticky)`, so a cache-skewed stale `base.css`
  cannot break the layering.
- Cache-busting: `base.html` links CSS with `?v={{ app_version }}` so files refetch
  together per release (the skew above was a real mixed-cache failure a user hit).
- Compact mobile matrix: tighter rows, smaller pills, thinner shift-colour rails.
- Short date on mobile: the date cell renders `<span class="date-full">` (full ISO,
  desktop) and `<span class="date-short">` (MM-DD via `strftime`, mobile) so the frozen
  area is narrow enough that ~4 people show before scrolling. `month_all.html` and
  `year_all.html` both updated.
