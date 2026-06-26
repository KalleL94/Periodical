# Design System Plan: "Operations board, not SaaS dashboard"

Status: steps 1-4 done and committed on `feat/design-system`; step 5 remaining.

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

1. [DONE] **Tokenise colour**: inline hex pulled into semantic CSS variables.
   No visual change. (commits e938f80, 1987fe3)
2. [DONE] **Fix semantic collisions**: admin role uses --role (#a78bfa violet),
   red reserved for danger. (2230591)
3. [DONE] **Replace accent + desaturate chrome**: Ant blue -> teal #4d8a9a via
   the --accent-rgb channel token, --ink #0e1216, statistics.html charts read
   CSS vars. (8b6e699, cb8cf40)
4. [DONE] **Typography**: Bricolage Grotesque (headings) + Inter (body) + IBM
   Plex Mono (numbers and short codes only). (6e95ba2)
5. [DONE] **Board treatment** of the matrix + login grid DNA:
   - week_all (person x day): today's column carries a vertical teal "now"
     rail on its trailing edge plus an accent flag under the header; date
     numbers and person IDs are monospace; hairlines unified to --line, header
     and sticky name-column dividers to --line-strong. (calendar.css)
   - month_all / year_all (day x row): today's row reads as a horizontal teal
     "now" rule across the board, date column monospace; sticky header and
     left column anchored with --line-strong. (tables.css)
   - login: the empty void becomes a faint slice of the schedule board
     (day-column-dominant grid, radial-masked calm pocket) with a single teal
     "now" line behind the card. login.html rewritten onto .login-* classes;
     inline styles removed. (components.css)
   - Verified on the :8001 dev container via browse (computed CSS + screenshots
     of /week, /month, /year and /login).

### Follow-ups not yet done
- Consolidate neutral greys and near-duplicate colours (#f44336, #4f9cf9,
  #4caf50, #f59e0b, #1976d2) into tokens.
- vacation.html / admin_vacation_user.html week-picker JS still hardcodes
  #3b82f6 for the selected state.
- Fonts load from Google Fonts; consider self-hosting for an internal app.
- Remove the temporary preview files in app/static/ (_design-preview.html,
  _accent_compare.html, _type_compare.html, _display_compare.html, _cmp_*.png).

### Done along the way
- The Ant-blue focus glow leaking on .date-picker-input:focus
  (rgba(0,123,255,.1)) now resolves from --accent-soft. (components.css)
- Monospace date column wrapped onto two lines (mono is wider than Inter and
  the ISO date's hyphens are break points). Fixed with white-space: nowrap +
  a trimmed font-size on .month-schedule .date-cell. (tables.css)
- The month/year matrix overflowed to the right (off-centre) when substitute
  columns pushed it past the 1175px main column. Fixed without widening it:
  .month-grid breaks out to the full viewport and uses justify-content: safe
  center, so the table is centred when it fits the viewport and left-aligned
  (page scrolls) once it is wider; .month-schedule uses width: min-content with
  a floor of main's content width, so it keeps its compact natural width and
  narrow months still fill main as before. No overflow container, so the sticky
  header is preserved. (layout.css)

A standalone preview of the target direction was built and reviewed before
implementation.
