/**
 * Currency helpers.
 *
 * Paise is the wire unit — Razorpay denominates every amount in the
 * smallest currency unit. Rupees is what an operator reads. All conversion
 * and INR formatting lives here so every component renders money the same
 * way and the paise/rupees boundary stays in one place.
 */

/** Smallest currency unit (paise) -> rupees. */
export function paiseToRupees(paise: number): number {
  return paise / 100;
}

/** Rupees -> paise, rounded so floating-point input can't drift the amount. */
export function rupeesToPaise(rupees: number): number {
  return Math.round(rupees * 100);
}

/** Format a paise amount as an en-IN rupee string, e.g. ₹1,499.00. */
export function formatInr(paise: number): string {
  return `₹${paiseToRupees(paise).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
