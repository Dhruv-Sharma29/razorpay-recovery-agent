/**
 * TopBar component.
 *
 * Purely presentational brand header for the Reflow console, plus the
 * light/dark theme toggle — no policy or pipeline logic here.
 */

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("theme") as Theme) ?? "light",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
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
