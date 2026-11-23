# Implementation Plan: Deduplicating "Hidden Gems" in Compass Report

This plan resolves the overlap where careers shown in the top 5 combined matches (`compass.top_matches`) can also appear in `"Hidden gems"` (`compass.hidden_gems`). It follows **Option A**, preserving the raw reference bundle parity tests and fixtures intact while verifying the deduplication behavior in `generateReport` and API integration tests.

---

### 1. Backend Scoring Logic (`backend/src/scoring/report.ts`)

* **Capture top combined career IDs:**
  Directly after `topInterests` (around line 116), define the set of career IDs included in `top_matches`:
  ```ts
  const topInterests = new Set(interestMatches.slice(0, 3).map((match) => match.career.id));
  const topCombined = new Set(combined.slice(0, 5).map((match) => match.career.id));
  ```

* **Filter `hidden_gems` against both sets (around lines 240–248):**
  Ensure candidates exclude both `topCombined` and `topInterests`, preserving the existing score and confidence criteria:
  ```ts
  hidden_gems: combined
    .filter(
      (match) =>
        !topCombined.has(match.career.id) &&
        !topInterests.has(match.career.id) &&
        match.score >= 65 &&
        match.confidence !== 'mixed',
    )
    .slice(0, 3)
    .map(toMatch),
  ```

* **Leave `watch_outs` as-is (lines 249–253):**
  Keep "Where the signals differ" unchanged, allowing it to highlight conflicting-signal paths regardless of their overall rank.

---

### 2. Frontend Copy (`frontend/src/pages/Report.tsx`)

* **Update section subtitle (line 249):**
  Align the description with the fact that top overall matches are excluded, not just top interest matches:
  * **From:**
    ```tsx
    description="Paths outside your top three interest matches that fit the combined profile."
    ```
  * **To:**
    ```tsx
    description="Paths outside your top matches that still fit your combined profile."
    ```

---

### 3. Tests & Fixtures (Option A)

* **Preserve Parity Fixtures Untouched (`backend/test/scoring.test.ts` & `backend/test/fixtures/scoring-parity.json`):**
  * Leave `scoring-parity.json` and the parity loop in `backend/test/scoring.test.ts` untouched to maintain the verifiable historical baseline against the upstream reference engine.
* **Add Unit Test for `generateReport` (`backend/test/scoring.test.ts`):**
  * Add a targeted test verifying that for a completed EQ stage report generated via `generateReport`, `compass.hidden_gems` contains no IDs from `compass.top_matches`.
* **API Integration Test (`backend/test/integration.test.ts`, around line 412):**
  * Verify the invariant on the published/fetched report response:
    ```ts
    const topIds = new Set(
      read.json().payload.compass.top_matches.map((m: { career_id: string }) => m.career_id),
    );
    assert.ok(
      read.json().payload.compass.hidden_gems.every(
        (m: { career_id: string }) => !topIds.has(m.career_id),
      ),
    );
    ```

---

### 4. Verification

* **Backend:**
  ```bash
  cd backend && npm run typecheck && npm test
  ```
  Ensure all unit, parity, and integration tests pass cleanly.
* **Frontend:**
  ```bash
  cd frontend && pnpm typecheck && pnpm test && pnpm build
  ```
* **Git diff check:**
  Confirm changes are isolated to [backend/src/scoring/report.ts](file:///home/kyle/fq/backend/src/scoring/report.ts), [frontend/src/pages/Report.tsx](file:///home/kyle/fq/frontend/src/pages/Report.tsx), [backend/test/integration.test.ts](file:///home/kyle/fq/backend/test/integration.test.ts), [backend/test/scoring.test.ts](file:///home/kyle/fq/backend/test/scoring.test.ts), and [docs/hiddengems.md](file:///home/kyle/fq/docs/hiddengems.md).
