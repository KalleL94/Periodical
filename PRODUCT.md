# Product

## Register

product

## Users

A single rotating team of about ten people working day, evening, and night
shifts plus on-call (Beredskap) in Sweden. They are not power users of software;
they open Periodical to settle one practical question, often on a phone, often
between or around shifts, sometimes late at night.

Two roles share the surface:

- **Team members** check their own schedule and their own pay (OB, on-call,
  overtime, vacation balance). They see only their own salary figures.
- **Admins** manage users, wages, rotation eras, substitutes, and settings, and
  can see everyone's figures.

The job to be done is the same for both, phrased from the member's seat:
*"When do I work, and what will I earn?"* with complete trust in the numbers.

## Product Purpose

Periodical turns a 10-week shift rotation and Swedish labour-law OB rules into a
clear schedule and a correct paycheck preview. It calculates inconvenient-hours
pay (OB1-OB5), on-call compensation, overtime, holiday handling, and vacation
balances under Handelns tjänstemannaavtal, then presents them so a team member
can read off the answer without doing their own math.

Success is the user getting the answer at a glance and not feeling the need to
double-check it by hand. The schedule matrix and the pay readout are the
product; everything else is in service of trusting those two.

## Brand Personality

An operations board, not a SaaS dashboard. Calm, exact, and instrument-like. The
voice is plain and factual, in Swedish, with no marketing tone and no cleverness
that competes with the data.

Three words: **precise, legible, trustworthy.**

The guiding emotional goal is **fast overview**: the answer to "when do I work /
what will I earn" should be visible immediately, not hunted for. Colour belongs
to the data (the shift pills); the chrome stays near-colourless so the numbers
read as the only chromatic event on the screen.

## Anti-references

- **Generic SaaS dashboard.** Dark background plus a single loud accent,
  hero-metric template cards, Ant Design default blue. This is the primary thing
  to avoid; the design deliberately inverts it by removing the loud accent and
  letting the data carry the colour.
- **Cream + serif + terracotta "warm editorial."** The wrong tone entirely for
  an operations tool; warmth is not the brand.
- **Overdesigned / decorative chrome.** Decorative motion, glassmorphism, and
  ornamental surfaces that compete with the schedule data for attention. Motion
  conveys state only.
- **Playful consumer-app styling.** Gradients, mascots, rounded bubble shapes;
  anything that undermines confidence in the figures.

## Design Principles

1. **Colour belongs to the data.** The shift pills own the spectrum; the chrome
   recedes. Never add another saturated accent next to the matrix.
2. **Numbers are instrument readouts.** Hard data (hours, pay, dates, shift
   codes) uses tabular monospace so columns align and figures read as
   measurements, not prose.
3. **Answer first.** Lead each surface with the question it answers (next shift,
   net pay) before any supporting detail. Fast overview beats completeness.
4. **Trust over flourish.** Consistency, legibility, and correctness outrank
   delight. The tool should disappear into the task.
5. **One meaning per colour.** Semantic colours do not collide (danger is money
   out, not also a role; the admin role has its own colour).

## Accessibility & Inclusion

- **WCAG AA contrast.** Body text at least 4.5:1, large text at least 3:1,
  against its background. Muted greys must still clear the bar.
- **Colour-blind safe.** Shift and status meaning is never carried by colour
  alone. Each shift pill shows its code, and status is reinforced by label or
  position, so the board is readable without colour discrimination.
- **Reduced motion.** Honour `prefers-reduced-motion`; every animation has a
  crossfade or instant fallback.
- **Keyboard and focus.** Clear focus rings and full keyboard navigation across
  tables and forms; the focus state uses the interaction accent, not the data
  palette.
