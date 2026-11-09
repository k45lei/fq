import type { Pay } from "../api/types";

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/**
 * A pay range, or whatever part of one we have.
 *
 * Every field is nullable because `career_pay` rows can be partial — and the whole object is
 * absent when the career wasn't found at all. Nothing here invents a figure.
 */
export function formatPay(pay: Pay): string | null {
  const money = (amount: number) =>
    new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: pay.currency || "USD",
      maximumFractionDigits: 0,
    }).format(amount);

  if (pay.low !== null && pay.high !== null) return `${money(pay.low)} – ${money(pay.high)}`;
  if (pay.median !== null) return `${money(pay.median)} median`;
  if (pay.low !== null) return `from ${money(pay.low)}`;
  if (pay.high !== null) return `up to ${money(pay.high)}`;
  return null;
}
