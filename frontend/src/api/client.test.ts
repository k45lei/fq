/**
 * Tests for the payment half of the API layer.
 *
 * The frontend's rule is that nothing outside `src/api/` may call `fetch`, or know a URL or
 * a status code. That rule is only worth anything if the layer itself is tested, because
 * the mistakes that matter here are in the URLs and the payloads: a checkout POSTed to the
 * wrong path, a discount code sent under the wrong key, a session id that isn't escaped.
 *
 * These use Node's built-in test runner and a stubbed `fetch`. No test framework is
 * installed in this project, and adding one for a handful of assertions about URL
 * construction would be more machinery than the tests.
 *
 * The stub records what was called and replies with a canned response, so the assertions
 * are about the *request* the client made — which is the part the backend actually sees.
 */

import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { api, ApiError } from "./client.ts";

// --------------------------------------------------------------------- the fetch stub

interface Call {
  url: string;
  method: string;
  body: string | undefined;
  headers: Record<string, string>;
  credentials: string | undefined;
}

const calls: Call[] = [];

/** What the next `fetch` replies with. Set by each test. */
let reply: { status?: number; body?: unknown; text?: string } = {};

/**
 * Replace the global `fetch` with a recorder.
 *
 * `globalThis.fetch` is captured at call time rather than at import time, so this works
 * regardless of module evaluation order.
 */
function stubFetch(): void {
  calls.length = 0;
  globalThis.fetch = (async (input: string, init?: RequestInit) => {
    calls.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: typeof init?.body === "string" ? init.body : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
      credentials: init?.credentials,
    });
    const status = reply.status ?? 200;
    const text =
      reply.text !== undefined
        ? reply.text
        : reply.body !== undefined
          ? JSON.stringify(reply.body)
          : "";
    return new Response(text, {
      status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
}

afterEach(() => {
  // Leave the global as we found it, so a test that forgets to stub can't silently pass.
  delete (globalThis as { fetch?: typeof fetch }).fetch;
});

/**
 * The one call that was made.
 *
 * Asserting there's exactly one, and returning it, is clearer than indexing `calls[0]`
 * everywhere — and it makes "the client made two requests" a failure rather than a silent
 * pass on the first one.
 */
function onlyCall(): Call {
  assert.equal(calls.length, 1, `expected one request, made ${calls.length}`);
  const call = calls[0];
  assert.ok(call, "no request was made");
  return call;
}

// --------------------------------------------------------------------- the tests

describe("pricing", () => {
  it("asks the server what the report costs", async () => {
    stubFetch();
    reply = { body: { amount_cents: 2999, currency: "usd" } };

    const pricing = await api.pricing();

    const call = onlyCall();
    assert.equal(call.url, "/api/pricing");
    assert.equal(call.method, "GET");
    // The number shown has to be the number charged, so it comes from the server.
    assert.equal(pricing.amount_cents, 2999);
    assert.equal(pricing.currency, "usd");
  });

  it("sends the ownership cookie", async () => {
    stubFetch();
    reply = { body: { amount_cents: 2999, currency: "usd" } };

    await api.pricing();

    // The cookie is what makes a response readable at all.
    assert.equal(onlyCall().credentials, "include");
  });
});

describe("createCheckout", () => {
  it("posts to the response's checkout path", async () => {
    stubFetch();
    reply = { body: { url: "https://checkout.stripe.com/c/pay/cs_1", session_id: "cs_1" } };

    const checkout = await api.createCheckout("resp-1");

    const call = onlyCall();
    assert.equal(call.url, "/api/responses/resp-1/checkout");
    assert.equal(call.method, "POST");
    assert.equal(checkout.url, "https://checkout.stripe.com/c/pay/cs_1");
    assert.equal(checkout.session_id, "cs_1");
  });

  it("sends no discount code when there isn't one", async () => {
    stubFetch();
    reply = { body: { url: "https://checkout.stripe.com/c/pay/cs_1", session_id: "cs_1" } };

    await api.createCheckout("resp-1");

    // An empty object, not `{"discount_code": ""}` — the backend treats a blank string as
    // a code that was supplied and can't be found.
    assert.deepEqual(JSON.parse(onlyCall().body ?? "{}"), {});
  });

  it("sends the discount code under the name the backend expects", async () => {
    stubFetch();
    reply = { body: { url: "https://checkout.stripe.com/c/pay/cs_1", session_id: "cs_1" } };

    await api.createCheckout("resp-1", "SUMMER20");

    // `discount_code`, not `discountCode`: the request schema forbids unknown fields, so
    // the wrong name is a 422 rather than a silently ignored code.
    assert.deepEqual(JSON.parse(onlyCall().body ?? "{}"), { discount_code: "SUMMER20" });
  });

  it("reports an invalid discount code with the server's message", async () => {
    stubFetch();
    reply = { status: 400, body: { detail: "that discount code isn't valid" } };

    await assert.rejects(
      () => api.createCheckout("resp-1", "NOPE"),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 400);
        // The message is one the backend wrote for a person to read.
        assert.equal(error.message, "that discount code isn't valid");
        return true;
      },
    );
  });

  it("reports an already-owned response as a conflict", async () => {
    stubFetch();
    reply = { status: 409, body: { detail: "you already have the full report for this response" } };

    await assert.rejects(
      () => api.createCheckout("resp-1"),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 409);
        return true;
      },
    );
  });

  it("reports an unconfigured Stripe as unavailable", async () => {
    stubFetch();
    reply = { status: 503, body: { detail: "STRIPE_SECRET_KEY is not set, so payments are unavailable." } };

    await assert.rejects(
      () => api.createCheckout("resp-1"),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 503);
        return true;
      },
    );
  });
});

describe("checkoutStatus", () => {
  it("asks about the session", async () => {
    stubFetch();
    reply = {
      body: { session_id: "cs_1", paid: true, entitled: true, response_id: "resp-1" },
    };

    const status = await api.checkoutStatus("cs_1");

    assert.equal(onlyCall().url, "/api/checkout/cs_1");
    assert.equal(status.paid, true);
    assert.equal(status.entitled, true);
    assert.equal(status.response_id, "resp-1");
  });

  it("escapes the session id", async () => {
    stubFetch();
    reply = { body: { session_id: "x", paid: false, entitled: false, response_id: null } };

    await api.checkoutStatus("cs_test/../evil");

    // A session id comes from the URL bar, so it can't be pasted into a path unescaped.
    assert.equal(onlyCall().url, "/api/checkout/cs_test%2F..%2Fevil");
  });

  it("reports a session that is not yet entitled", async () => {
    stubFetch();
    // The webhook hasn't landed yet — the return page polls while this is the answer.
    reply = { body: { session_id: "cs_1", paid: true, entitled: false, response_id: "resp-1" } };

    const status = await api.checkoutStatus("cs_1");

    assert.equal(status.paid, true);
    assert.equal(status.entitled, false);
  });

  it("reports a session that names no response", async () => {
    stubFetch();
    reply = { body: { session_id: "cs_1", paid: false, entitled: false, response_id: null } };

    const status = await api.checkoutStatus("cs_1");

    assert.equal(status.response_id, null);
  });
});

describe("the error contract", () => {
  it("turns a field-error list into a readable message", async () => {
    stubFetch();
    reply = { status: 422, body: { detail: [{ msg: "field required" }] } };

    await assert.rejects(
      () => api.pricing(),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 422);
        assert.equal(error.message, "field required");
        return true;
      },
    );
  });

  it("falls back when the error body isn't JSON", async () => {
    stubFetch();
    reply = { status: 500, text: "<html>Internal Server Error</html>" };

    await assert.rejects(
      () => api.pricing(),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 500);
        assert.equal(error.message, "Request failed (500)");
        return true;
      },
    );
  });

  it("reports an unreachable server as status zero", async () => {
    globalThis.fetch = (async () => {
      throw new TypeError("Failed to fetch");
    }) as typeof fetch;

    await assert.rejects(
      () => api.pricing(),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        // 0 is not a status the server sent — it means the request never arrived.
        assert.equal(error.status, 0);
        assert.match(error.message, /Could not reach the server/);
        return true;
      },
    );
  });
});
