import { ApiError } from "../../api/client";
import { useCreateCheckout, usePricing } from "../../api/queries";
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

  if (!sections.length) {
    return null;
  }

  const startCheckout = async () => {
    if (!responseId) {
      return;
    }
    try {
      const session = await checkout.mutateAsync({ responseId });
      window.location.assign(session.url);
    } catch {
      // The mutation error is rendered below, with the answers still available.
    }
  };

  return (
    <section className="compass-banner mt-12 rounded-3xl p-8 text-white sm:p-10">
      <p className="text-sm font-semibold">Get the complete picture</p>
      <h2 className="mt-3 text-3xl font-bold">Interests are just the beginning.</h2>
      <p className="mt-4 max-w-2xl text-white/90">
        Unlock the strengths and EQ assessments, then see how all three signals combine in your
        Compass report.
      </p>
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        {["Strengths · 76 questions", "EQ · 30 questions", "Combined Compass report"].map(
          (label) => (
            <p
              key={label}
              className="rounded-xl border border-white/25 bg-white/10 p-4 text-sm font-semibold"
            >
              {label}
            </p>
          ),
        )}
      </div>
      <button
        type="button"
        onClick={startCheckout}
        disabled={checkout.isPending || !responseId || !pricing}
        className="mt-7 rounded-full bg-white px-7 py-4 font-bold text-[#3244c7] disabled:opacity-50"
      >
        {checkout.isPending
          ? "Opening checkout…"
          : pricing
            ? `Unlock the remaining assessments — ${formatPrice(pricing.amount_cents, pricing.currency)}`
            : "Loading price…"}
      </button>
      <p className="mt-3 text-xs text-white/85">One payment for this attempt. No subscription.</p>
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
