# crystalBALL — interface conventions

What the UI is made of and why, so the next screen matches the existing ones without
re-deciding anything. Tokens live in `frontend/src/index.css`; this explains the choices.

Last updated: 2026-10-02

---

## Tone

The audience is sixteen to twenty-two. The product has to read as **trustworthy without reading
as a textbook**, and as **honest about its own limits** — it's career guidance, not a verdict.
That shapes the copy as much as the visuals:

- Short sentences, concrete nouns, no jargon. "Things to watch", not "Potential challenges".
- Say what something *isn't*. "A starting point, not a shortlist." "A guide, not a verdict."
- Never imply more certainty than the data supports. The free report is a real result, not a
  crippled one, and the paywall teaser says so.

## Type

| Role | Face | Where |
| --- | --- | --- |
| Display | Source Serif 4 at 600 (`font-display font-semibold`) | Page titles, section headings, the wordmark |
| Everything else | Inter (`font-sans`) | Body, labels, questions, controls |

The serif appears **only at display sizes**. It carries the credibility; using it for body text
would cost legibility and buy nothing. Both faces have real fallback stacks, so an offline font
load degrades rather than breaks.

A *text* serif, not a display one. Display serifs (Instrument Serif, Playfair and friends) are
drawn for posters — narrow, tall ascenders, small x-height — and at heading sizes on screen they
read as tall and hard to scan. Source Serif 4 has a high x-height, generous widths and an
optical-size axis that thickens strokes as the size grows.

Two rules that follow from using a serif at all: **weight, not tracking.** Serif headings take
`font-semibold`; `tracking-tight` cramps them and is for the sans face only. And leading stays
near `1.1` rather than tighter, because serif ascenders and descenders need the room.

Question prompts are `text-2xl font-medium` in the sans face — they're read, not admired.

## Colour

A monochromatic palette based on pure black and white with neutral grays.

| Token | Value | Use |
| --- | --- | --- |
| `canvas` | `#FFFFFF` | Page background. |
| `surface` | `#FFFFFF` | Cards and inputs. |
| `warm` | `#F5F5F5` | Light gray for alternate bands (e.g. paywall teaser). |
| `ink` | `#000000` | Headings and emphasis. |
| `muted` | `#666666` | Secondary text and body copy. |
| `line` | `#DDDDDD` | Borders and meter tracks. |
| `accent` | `#000000` | Buttons, selection, progress, the data hue. |
| `accent-strong` | `#333333` | Hover. |
| `accent-weak` | `#AAAAAA` | De-emphasised bars only, never behind text. |
| `accent-soft` | `#F2F2F2` | Selected backgrounds. |

### Contrast and Emphasis

On a pure white scheme, emphasis comes from the weight of the black and the depth of the grays. Headings are pure black (21:1 on canvas); `muted` is 5.9:1, comfortably AA.

**One accent tone, doing one job: "this is active or selected."** In this scheme, the accent is black, creating a high-contrast visual anchor for primary actions.

### One known soft spot

`accent-weak` against `line` (bar versus track) is ~1.6:1, below the 3:1 you'd want for a
meaningful graphic. It's acceptable here because every bar is **directly labelled with its
percentage** — the contrast carries emphasis, not information — and because the bars that do
carry the reading use `accent` at 21:1 against the track. If the labels ever come off, this
has to be re-stepped.

## Data visualisation

Already settled, and worth not relitigating (the reasoning is in `components/report/`):

- **Colour encodes nothing in the trait chart.** Six values of one measure across six
  categories — length is the encoding. A six-hue palette would be decoration pretending to be
  information.
- The three traits forming the Holland code are distinguished by **weight and an explanatory
  line**, never colour alone, so the distinction survives greyscale and screen readers.
- Every bar is **directly labelled** with its trait and percentage, which is why there's no
  axis, no gridlines and no legend. A single series names itself.
- A fit score is **a number with a thin meter**, not a chart — it's one value.

## Components

| Component | Responsibility |
| --- | --- |
| `Layout` | Page shell: wordmark, measure, footer. `width="narrow"` for forms, `"wide"` for the report |
| `ProgressBar` | Position within the current section, with the section named |
| `QuestionCard` | Renders one question of any type. Presentational — knows nothing about the API |
| `report/TraitBars` | The six trait scores |
| `report/CareerCard` | One suggestion: fit, rationale, pay when known |
| `report/PaywallTeaser` | What the paid sections add |

Cards are `rounded-2xl border border-line bg-surface shadow-card`. Controls are `rounded-xl`.
Nothing is square.

## Interaction

- **Selected** states use `border-accent bg-accent-soft` plus a filled marker — shape *and*
  colour, never colour alone.
- **Multi-select blocks at the limit** rather than letting someone over-select and rejecting
  the submission afterwards. The count ("2 of 3 selected") is always visible.
- **Next is disabled until the question is answered.** The client-side check mirrors the
  server's rules but is not a security boundary; the server validates independently.
- **Keyboard**: number keys pick an option or a scale point, Enter advances. Options show their
  shortcut on pointer-capable widths. Ignored while typing in a textarea or input.
- **Movement**: questions animate in with `.animate-rise`. Without it the prompt swaps in place
  and it's easy to miss that anything changed. Suppressed under `prefers-reduced-motion`.

## Absence

Where data is missing, the interface **says nothing** rather than drawing attention to the hole.
A career with no pay figure shows no pay row — no "salary unavailable" placeholder. The absence
is the normal case, and flagging it would imply breakage.

The exception is **provenance**: a placeholder pay figure says "indicative only" on the card
itself, not in a footnote, because a skimmer would read an unqualified number as sourced.

## Print

The PDF is a **separate template** (`backend/app/templates/report.html`), not the React markup
run through a print stylesheet. A document that can't scroll, can't hover and may come out in
black and white wants different decisions, and one template serving both would compromise each.

What differs on purpose:

- **System fonts only.** WeasyPrint would fetch a webfont at render time, which turns an
  offline render into a silent fallback and a slow one into a hung request.
- `@page` rules for A4 and page numbering; `break-inside: avoid` on cards and sections so a
  heading never strands at the foot of a page.
- No radar. The bars carry the same information and survive a monochrome printer.

## Not done yet

- No dark mode. The tokens are structured for it (swap the six colour values) but no dark
  variants are defined.
- No focus-visible styling beyond the browser default.
- No component tests. The frontend has no test runner at all.
