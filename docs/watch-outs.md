# "Where the signals differ" section

## What it is

A section on the EQ-stage report (`frontend/src/pages/Report.tsx`) that surfaces careers where the three assessment signals disagree, instead of quietly averaging them away. It shows each career's name, combined fit score, and its confidence label. Copy: "These paths score differently across the assessments. Explore what that means for you."

## How it is computed

Only exists on EQ-stage reports, where the Compass is built (`backend/src/scoring/report.ts`, `compass.watch_outs`):

1. `combined` = careers ranked by `round(interests * 0.4 + strengths * 0.3 + eq * 0.3)` with per-signal scores kept (`backend/src/scoring/engine.ts`, `combine`).
2. Per career: `spread = max(interests, strengths, eq) − min(interests, strengths, eq)` (all 0–100).
3. `confidence`: `high` if spread ≤ 15, `medium` if ≤ 30, else `mixed` (`engine.ts`, `confidence`).
4. `watch_outs` = `combined.slice(0, 10)` filtered to `confidence === 'mixed'`, sliced to 3 (`report.ts`, lines ~251–256).

## Behavior worth knowing

- Every entry is mixed-confidence by construction; the UI label is redundant with the filter.
- Not deduplicated against top matches or hidden gems — a top-ranked career can appear here, which is the point (conflicting signals matter regardless of overall rank). See `docs/hiddengems.md`.
- Only the top 10 combined-ranked careers are eligible; careers ranked below are never watch-outs.
- Only shown when at least one such career exists; absent otherwise.
- The API payload includes per-signal scores (`signals.interests/strengths/eq`) on each match, but this section does not render them (the top-matches banner does).
- Hidden gems exclude `mixed` matches, so watch-outs is the only section where conflicting paths appear.
- The scoring parity fixture replicates the same logic (`backend/test/scoring.test.ts`, lines ~44–53).
