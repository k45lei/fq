/**
 * What the paid sections would add.
 *
 * Takes the locked sections rather than hardcoding them, so it can never offer a section the
 * reader has already answered — the server decides which sections are locked, and this
 * component only reports on that.
 *
 * Deliberately honest about what's already been given: the free report is a real result, not a
 * crippled one. The teaser names what more information buys rather than implying the current
 * report is incomplete.
 *
 * The button is the checkout. It posts to the server, which creates a Stripe Checkout Session
 * and hands back a URL — then the browser leaves for Stripe. It's a navigation, not a fetch,
 * because the payment happens on Stripe's page.
 */

import { useState } from "react";

import { ApiError } from "../../api/client";
import { useCreateCheckout, useApplyDiscount, usePricing } from "../../api/queries";
import type { QuestionCategory } from "../../api/types";
import { SECTION_META } from "../../lib/sections";

interface Props {
  sections: QuestionCategory[];
  /** The response being upgraded. Without it there's nothing to price or charge for. */
  responseId?: string;
}

export default function PaywallTeaser({ sections, responseId }: Props) {
  const { data: pricing } = usePricing();
  const checkout = useCreateCheckout();
  const applyDiscount = useApplyDiscount();
  const [code, setCode] = useState("");

  if (sections.length === 0) return null;

  const price = pricing ? formatPrice(pricing.amount_cents, pricing.currency) : null;

  const startCheckout = async () => {
    if (!responseId) return;
    try {
      if (code.trim()) {
        await applyDiscount.mutateAsync({ responseId, discountCode: code.trim() });
        // Discount applied, clear the field.
        setCode("");
        // The query invalidation in the mutation will refresh data.
      } else {
        const session = await checkout.mutateAsync({ responseId });
        // A full-page navigation, not a client-side route change: Stripe's page is another
        // origin, and the browser has to be able to come back to ours.
        window.location.assign(session.url);
      }
    } catch {
      // The error is shown below; nothing to do here.
    }
  };

  return (
    <section className="mt-14 rounded-2xl border border-line bg-warm p-7">
      <h2 className="font-display text-3xl font-semibold">Go deeper</h2>
      <p className="mt-2 text-sm text-muted text-pretty">
        Add your answers to the remaining sections. Personalized results are coming soon.
      </p>

      <ul className="mt-5 space-y-4">
        {sections.map((section) => {
          const meta = SECTION_META[section];
          return (
            <li key={section} className="flex gap-3">
              <span aria-hidden className="mt-0.5 text-muted">
                &#128274;
              </span>
              <div>
                <p className="font-medium">{meta.label}</p>
                <p className="mt-0.5 text-sm text-muted text-pretty">{meta.detail}</p>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="mt-7">
        <label className="block">
          <span className="text-sm font-medium">Discount code</span>
          <input
            type="text"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder="Optional"
            className="mt-1.5 w-full rounded-xl border border-line bg-surface px-4 py-2.5 outline-none transition focus:border-accent"
          />
        </label>

        {/* Apply discount button appears when user types a code */}
        {code.trim() && (
          <button
            type="button"
            onClick={startCheckout}
            disabled={applyDiscount.isPending || !responseId}
            className="mt-2 w-full rounded-xl bg-accent px-5 py-3.5 font-medium text-white transition hover:bg-accent-strong disabled:opacity-50"
          >
            Apply discount
          </button>
        )}

        {/* Checkout button always visible */}
        <button
          type="button"
          onClick={startCheckout}
          disabled={checkout.isPending || !responseId}
          className="mt-4 w-full rounded-xl bg-accent px-5 py-3.5 font-medium text-white transition hover:bg-accent-strong disabled:opacity-50"
        >
          {checkout.isPending
            ? "Opening checkout…"
            : price
              ? `Unlock the full report — ${price}`
              : "Unlock the full report"}
        </button>

        {price && (
          <p className="mt-2 text-center text-xs text-muted">
            One payment. It stays unlocked on this device.
          </p>
        )}

        {(checkout.error || applyDiscount.error) && (
          <p className="mt-3 text-center text-sm text-red-700">
            {(checkout.error ?? applyDiscount.error) instanceof ApiError
              ? (checkout.error ?? applyDiscount.error)?.message
              : "Couldn't start the payment. Please try again."}
          </p>
        )}
      </div>
    </section>
  );
}

/**
 * The price, formatted.
 *
 * The amount comes from the server — the number shown has to be the number charged — and
 * this only turns cents into a currency string.
 */
function formatPrice(amountCents: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency.toUpperCase(),
    }).format(amountCents / 100);
  } catch {
    // An unknown currency code shouldn't take the button down with it.
    return `${(amountCents / 100).toFixed(2)} ${currency.toUpperCase()}`;
  }
}
