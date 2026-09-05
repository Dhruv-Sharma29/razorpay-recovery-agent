/**
 * Minimal toast: a hook plus the region that renders it.
 *
 * No dependency — a toast is a list, a timer, and a live region, and pulling
 * in a library for that would be more code than this file.
 *
 * The region is a polite live region so a confirmation is announced without
 * interrupting whatever a screen-reader user is currently reading.
 */

import { useCallback, useRef, useState } from "react";

export type ToastTone = "ok" | "hold" | "stop";

export interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
}

const DISMISS_MS = 4000;

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (message: string, tone: ToastTone = "ok") => {
      const id = nextId.current++;
      setToasts((current) => [...current, { id, message, tone }]);
      // Auto-dismiss so a confirmation never becomes permanent furniture.
      window.setTimeout(() => dismiss(id), DISMISS_MS);
    },
    [dismiss],
  );

  return { toasts, push, dismiss };
}

interface ToasterProps {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}

export function Toaster({ toasts, onDismiss }: ToasterProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="toaster" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className="toast"
          data-tone={toast.tone}
          data-testid="toast"
        >
          <span className="toast__message">{toast.message}</span>
          <button
            type="button"
            className="toast__close"
            onClick={() => onDismiss(toast.id)}
            aria-label="Dismiss notification"
            data-testid="toast-dismiss"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
