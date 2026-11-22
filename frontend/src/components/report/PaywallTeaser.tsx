import { ApiError } from "../../api/client";
import { useState } from "react";
import { useApplyDiscount, useCreateCheckout, usePricing } from "../../api/queries";
import type { QuestionCategory } from "../../api/types";

export const formatPrice = (amountCents: number, currency: string): string => {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: currency.toUpperCase(),
  }).format(amountCents / 100);
};

export default function PaywallTeaser({
  sections,
  responseId,
}: {
  sections: QuestionCategory[];
  responseId?: string;
}) {
  const { data: pricing, error: pricingError, refetch } = usePricing();
  const checkout = useCreateCheckout();
  const applyDiscount = useApplyDiscount();
  const [discountCode, setDiscountCode] = useState("");

  if (!sections.length) {
    return null;
  }

  const startCheckout = async () => {
    if (!responseId) {
      return;
    }
    try {
      const session = await checkout.mutateAsync({
        responseId,
        discountCode: discountCode.trim() || undefined,
      });
      window.location.assign(session.url);
    } catch {
      // The mutation error is rendered below, with the answers still available.
    }
  };

  const apply = async () => {
    if (!responseId || !discountCode.trim()) {
      return;
    }
    try {
      await applyDiscount.mutateAsync({ responseId, discountCode: discountCode.trim() });
      setDiscountCode("");
    } catch {
      // The mutation error is rendered below.
    }
  };

  return (
    <section id="unlock" className="upsell-banner">
      <p className="upsell-pill">The full picture</p>
      <h2>Interests alone don’t tell the whole story</h2>
      <p className="upsell-subtitle">
        Unlock the strengths and EQ assessments, then see how all three signals combine in your
        Compass report.
      </p>
      <div className="upsell-cards">
        {[
          {
            title: "Strengths",
            icon: "✦",
            description: "Discover your work style and where your talents shine.",
          },
          {
            title: "EQ",
            icon: "♡",
            description: "Understand how you handle emotions, people, and pressure.",
          },
          {
            title: "Compass Report",
            icon: "⌾",
            description: "All three lenses combined into one ranked career list.",
          },
        ].map((card, index) => (
          <div key={card.title} className={`upsell-subcard${index === 2 ? " featured" : ""}`}>
            <div className="upsell-subcard-icon" aria-hidden="true">
              {card.icon}
            </div>
            <h3>{card.title}</h3>
            <p>{card.description}</p>
          </div>
        ))}
      </div>
      <div className="upsell-discount">
        <label htmlFor="discount-code" className="upsell-discount-label">
          Discount code
        </label>
        <div className="upsell-discount-row">
          <input
            id="discount-code"
            type="text"
            value={discountCode}
            onChange={(event) => setDiscountCode(event.target.value)}
            placeholder="Enter code"
            autoComplete="off"
          />
          <button
            type="button"
            onClick={apply}
            disabled={applyDiscount.isPending || !responseId || !discountCode.trim()}
            className="btn-white"
          >
            {applyDiscount.isPending ? "Applying…" : "Apply code"}
          </button>
        </div>
        {applyDiscount.error && (
          <p role="alert" className="mt-3 text-sm">
            {applyDiscount.error instanceof ApiError
              ? applyDiscount.error.message
              : "That code could not be applied. Please try again."}
          </p>
        )}
        {applyDiscount.isSuccess && (
          <p role="status" className="mt-3 text-sm">
            Code applied — the remaining assessments are unlocked.
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={startCheckout}
        disabled={checkout.isPending || !responseId || !pricing}
        className="btn-white"
      >
        {checkout.isPending
          ? "Opening checkout…"
          : pricing
            ? `Unlock the remaining assessments — ${formatPrice(pricing.amount_cents, pricing.currency)}`
            : "Loading price…"}
      </button>
      <p className="upsell-note">One payment for this attempt. No subscription.</p>
      {pricingError && (
        <p role="alert" className="mt-3 text-sm">
          Pricing is unavailable.{" "}
          <button type="button" onClick={() => refetch()} className="underline">
            Try again
          </button>
        </p>
      )}
      {checkout.error && (
        <p role="alert" className="mt-3 text-sm">
          {checkout.error instanceof ApiError
            ? checkout.error.message
            : "Checkout could not start. Please try again."}
        </p>
      )}
    </section>
  );
}
