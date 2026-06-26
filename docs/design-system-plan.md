# Design System Plan: "Operations board, not SaaS dashboard"

Status: in progress. Owner: design pass on `feat/design-system`.

## Thesis

Periodical tracks shift rotation and OB (inconvenient-hours) compensation for a
team working nights and on-call. The user's only real question is *"when do I
work and what will I earn?"* with total trust in the numbers. The distinctive
asset is the colour-coded schedule matrix, which is a departure / dispatch
board.

Design direction: **colour belongs to the data; the chrome is near-colourless;
numbers are instrument readouts.** This is the deliberate inverse of the common
"dark background + one loud accent" default. Because the shift pills already own
the full spectrum (admins configure each shift type's colour), the chrome must
recede instead of adding yet another saturated colour to a busy grid.

## Current problems this fixes

- The accent (`#40a9ff` / `#1890ff`) is the Ant Design default blue. The primary
  brand colour is a framework default.
- Multiple competing colours scattered as inline hex across templates: several
  blues (`#3b82f6`, `#40a9ff`, `#4f9cf9`, `#1890ff`), several reds (`#ef4444`,
  `#f44336`, `#dc2626`), loose oranges/purples/yellows. ~134 inline hex values,
  ~40 distinct, no central system.
- Semantic colour collision: red means "money out / danger" on the dashboard but
  also "Admin role" in the user list. Same colour, two meanings.

## Token system

### Colour (the frame: near-colourless chrome + separated semantics + data)

| Token | Value | Role |
|-------|-------|------|
| `--ink` | `#0e1216` | base background (more neutral graphite so rainbow pills read true) |
| `--panel` | `#141b22` | raised instrument surface |
| `--panel-2` | `#19222b` | nested surface |
| `--line` | `rgba(255,255,255,.07)` | hairline |
| `--line-strong` | `rgba(255,255,255,.14)` | structural hairline |
| `--text` | `#e6eef6` | primary text |
| `--muted` | `#8b98a6` | secondary text |
| `--faint` | `#5d6873` | tertiary / labels |
| `--accent` | `#4d8a9a` | **interaction only** (focus, active nav, primary button). Muted instrument teal, never Ant blue, never next to data |
| `--accent-soft` | `rgba(77,138,154,.16)` | focus ring / active wash |
| `--danger` | `#ef4444` | destructive / money out **only** |
| `--up` | `#5bbf8a` | money in |
| `--role` | `#a78bfa` | admin role, its own colour (fixes the red collision) |

Data pills stay a configurable rainbow owned by the schedule (admin-set
`shift.color`); the system does not override them, it only quiets everything
around them.

### Typography (one numeric language + personality)

- **Display** (headings, hero numbers): Space Grotesk. Technical character
  without shouting.
- **Body / labels**: Inter (kept).
- **All hard data**: IBM Plex Mono with tabular figures. Resolves today's two
  numeric languages (monospace `td.num` vs Inter elsewhere) into one. Numbers
  read as instrument readouts.
- **Scale**: 12 / 14 / 16 / 20 / 24 / 32 with deliberate weights. The
  12 -> 16 -> 20 step already in the summary cards is the seed.

### Layout + signature

The signature is the matrix as a dispatch board: sticky header row + sticky name
column, monospace codes, hairline grid, a "now" line marking today's column.
Login carries grid DNA (a faint slice of the board behind the card instead of
empty void). Stat cards become an instrument cluster of monospace readouts.

## Critique vs templated defaults

The three current AI-default looks are: cream + serif + terracotta; near-black +
one loud accent; broadsheet hairlines. This plan sits near #2 but **inverts** it:
it removes the single loud accent and makes the chrome near-colourless so the
data is the only chromatic event. The monospace instrument numerals and the
board framing are subject-specific. It avoids the default by turning it inside
out, not by accidentally resembling it.

## Implementation order

1. **Tokenise colour** (this step): pull inline hex into semantic CSS variables.
   Pure refactor, no visual change. Foundation for everything.
2. **Fix semantic collisions**: give admin role its own `--role`; reserve red.
3. **Replace accent + desaturate chrome** to the values above.
4. **Typography**: add Space Grotesk + IBM Plex Mono webfonts, apply mono to all
   data, set the scale.
5. **Board treatment** of the matrix (sticky, hairlines, now-line) + login grid
   DNA.

A standalone preview of the target direction was built and reviewed before
implementation.
