import assert from "node:assert/strict";
import { test } from "node:test";

const storage = new Map<string, string>();
Object.defineProperty(globalThis, "window", {
  value: {
    localStorage: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
    },
  },
});

const { useQuestionnaireStore } = await import("./questionnaire");

test("returning from checkout retains drafts for the same attempt", () => {
  const store = useQuestionnaireStore.getState();
  store.begin("paid-attempt");
  store.setAnswer("question", { numeric: 4 });
  store.complete("free-report");
  store.unlock("paid-attempt");

  const state = useQuestionnaireStore.getState();
  assert.deepEqual(state.answers, { question: { numeric: 4 } });
  assert.equal(state.reportId, null);
  assert.equal(state.currentQuestionId, null);
});

test("an older checkout return cannot copy another attempt's draft answers", () => {
  const store = useQuestionnaireStore.getState();
  store.begin("newer-attempt");
  store.setAnswer("question", { numeric: 5 });
  store.unlock("older-paid-attempt");

  const state = useQuestionnaireStore.getState();
  assert.equal(state.responseId, "older-paid-attempt");
  assert.deepEqual(state.answers, {});
  const persisted = JSON.parse(storage.get("futurequest.questionnaire")!);
  assert.deepEqual(persisted.state.answers, {});
});
