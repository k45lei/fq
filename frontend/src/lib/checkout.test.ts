import assert from "node:assert/strict";
import { test } from "node:test";
import { checkoutNextStep } from "./checkout.ts";
const paid = { entitled: true, response_id: "attempt" };
test("a paid visitor with an existing free report continues the questionnaire", () => {
  assert.deepEqual(checkoutNextStep(paid, { completed_sections: ["interests"] }, { id: "free" }), { kind: "continue", responseId: "attempt" });
});
test("a report after strengths does not skip unanswered EQ", () => {
  assert.deepEqual(checkoutNextStep(paid, { completed_sections: ["interests", "strengths"] }, { id: "full-pending" }), { kind: "continue", responseId: "attempt" });
});
test("a completed attempt can open its pending results", () => {
  assert.deepEqual(checkoutNextStep(paid, { completed_sections: ["interests", "strengths", "eq"] }, { id: "final" }), { kind: "report", reportId: "final" });
});
test("unpaid or refunded visitors and incomplete loading states never redirect", () => {
  assert.equal(checkoutNextStep({ ...paid, entitled: false }, { completed_sections: [] }, undefined), null);
  assert.equal(checkoutNextStep(paid, undefined, { id: "free" }), null);
});
