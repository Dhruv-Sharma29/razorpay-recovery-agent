import { useEffect, useState } from "react";

export function formatRemaining(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
  const days = Math.floor(totalSeconds / 86_400);
  const hours = Math.floor((totalSeconds % 86_400) / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export interface PendingRetryCountdownProps {
  scheduledFor: string;
  auditId: string;
  inline?: boolean;
}

export default function PendingRetryCountdown({
  scheduledFor,
  auditId,
  inline = false,
}: PendingRetryCountdownProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const target = Date.parse(scheduledFor);
  const remaining = Number.isNaN(target) ? null : target - now;
  const label =
    remaining === null
      ? "Retry time unavailable"
      : remaining <= 0
        ? "Ready to retry"
        : `Retry in ${formatRemaining(remaining)}`;

  return (
    <span
      className={inline ? "chain__value data-mono" : "cases__retry-countdown data-mono"}
      data-testid={`retry-countdown-${auditId}`}
      aria-live="polite"
    >
      {label}
    </span>
  );
}
