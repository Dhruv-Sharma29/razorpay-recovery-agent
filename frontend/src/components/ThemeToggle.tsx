/**
 * Light/dark toggle.
 *
 * Seeds from the attribute the inline script in index.html already set
 * before first paint, so React's first render agrees with what is on
 * screen and the theme never flashes.
 */

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

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

export function useTheme() {
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

export default function ThemeToggle() {
  const { theme, toggle } = useTheme();

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggle}
      aria-label="Toggle theme"
      data-testid="theme-toggle"
    >
      {theme === "light" ? "Dark mode" : "Light mode"}
    </button>
  );
}
