/**
 * TopBar component.
 *
 * Purely presentational brand header for the Reflow console, plus the
 * light/dark theme toggle — no policy or pipeline logic here.
 */

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

/**
 * Seeds from the attribute the inline script in index.html already set
 * before paint, so React's first render agrees with what's on screen.
 * Falls back to stored value, then to the OS preference.
 */
function initialTheme(): Theme {
  const painted = document.documentElement.dataset.theme;
  if (painted === "light" || painted === "dark") return painted;

  try {
    const stored = localStorage.getItem("theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage blocked (private mode) — fall through to the OS preference.
  }

  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("theme", theme);
    } catch {
      // Preference just won't persist; the toggle still works this session.
    }
  }, [theme]);

  return {
    theme,
    toggle: () => setTheme((t) => (t === "light" ? "dark" : "light")),
  };
}

export default function TopBar() {
  const { theme, toggle } = useTheme();

  return (
    <div className="topbar">
      <div className="topbar__brand">
        <span className="topbar__mark" aria-hidden="true">
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <rect x="2" y="5" width="20" height="14" rx="3" fill="currentColor" />
            <rect x="2" y="9" width="20" height="3" fill="#0a1a3f" />
          </svg>
        </span>
        <span className="topbar__name">Reflow</span>
        <span className="topbar__tagline">Failed-Payment Recovery Console</span>
      </div>
      <button
        className="theme-toggle"
        onClick={toggle}
        aria-label="Toggle theme"
        data-testid="theme-toggle"
      >
        {theme === "light" ? "Dark mode" : "Light mode"}
      </button>
    </div>
  );
}
