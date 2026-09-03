/**
 * CategoryChart component.
 *
 * Split into its own module so recharts (~360 kB) lands in a separate
 * chunk that is only fetched once there is something to plot. Display
 * only — no policy logic.
 */

import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface CategoryStat {
  category: string;
  recoveryRate: number;
}

interface CategoryChartProps {
  data: CategoryStat[];
}

export default function CategoryChart({ data }: CategoryChartProps) {
  return (
    // Height scales with category count and interval={0} forces every
    // tick, so recharts can't silently drop labels it predicts would
    // overlap.
    <ResponsiveContainer width="100%" height={Math.max(160, data.length * 42)}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 8, right: 16, bottom: 0, left: 0 }}
      >
        <XAxis
          type="number"
          domain={[0, 100]}
          unit="%"
          tick={{ fontSize: 11, fill: "var(--text-secondary)" }}
          stroke="var(--border-strong)"
        />
        <YAxis
          type="category"
          dataKey="category"
          width={160}
          interval={0}
          tick={{ fontSize: 12, fill: "var(--text-secondary)" }}
          stroke="var(--border-strong)"
        />
        <Tooltip
          formatter={(v) => `${v}%`}
          cursor={{ fill: "rgba(36, 82, 232, 0.06)" }}
          contentStyle={{
            background: "var(--bg-card)",
            border: "1px solid var(--border-default)",
            borderRadius: "8px",
            color: "var(--text-primary)",
          }}
        />
        <Bar
          dataKey="recoveryRate"
          fill="var(--brand-primary)"
          radius={[0, 4, 4, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
