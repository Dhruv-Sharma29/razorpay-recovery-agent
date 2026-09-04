/** Shared display formatting. Paise are the wire unit; rupees are read. */

export function formatRupees(paise: number | null | undefined): string {
  const value = typeof paise === "number" ? paise : 0;
  return `₹${(value / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Compact form for headline tiles, e.g. ₹4.3L. */
export function formatRupeesCompact(paise: number | null | undefined): string {
  const rupees = (typeof paise === "number" ? paise : 0) / 100;
  if (rupees >= 10_000_000) return `₹${(rupees / 10_000_000).toFixed(2)}Cr`;
  if (rupees >= 100_000) return `₹${(rupees / 100_000).toFixed(2)}L`;
  if (rupees >= 1_000) return `₹${(rupees / 1_000).toFixed(1)}K`;
  return `₹${rupees.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export function formatPercent(rate: number | null | undefined): string {
  const value = typeof rate === "number" ? rate : 0;
  return `${(value * 100).toFixed(1)}%`;
}

/** Turn a snake_case category into something readable. */
export function humanize(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
