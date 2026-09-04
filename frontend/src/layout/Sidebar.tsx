/**
 * Sidebar navigation.
 *
 * Batch-first information architecture: Overview is the measured-money
 * dashboard, Cases is the per-record audit chain, Agent is the model's
 * advisory output. Presentational only.
 */

export type ViewKey = "overview" | "cases" | "agent";

interface NavItem {
  key: ViewKey;
  label: string;
  hint: string;
}

const NAV: NavItem[] = [
  { key: "overview", label: "Overview", hint: "Batch results and measured recovery" },
  { key: "cases", label: "Cases", hint: "Every record with its decision chain" },
  { key: "agent", label: "Agent", hint: "What the model contributed" },
];

interface SidebarProps {
  active: ViewKey;
  onNavigate: (view: ViewKey) => void;
}

export default function Sidebar({ active, onNavigate }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="2" y="5" width="20" height="14" rx="3" fill="currentColor" />
            <rect x="2" y="9" width="20" height="3" fill="#0f1420" />
          </svg>
        </span>
        <span className="sidebar__brand-text">
          <span className="sidebar__name">Reflow</span>
          <span className="sidebar__tagline">AI Revenue Recovery</span>
        </span>
      </div>

      <nav className="sidebar__nav" aria-label="Primary">
        {NAV.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`sidebar__link${
              active === item.key ? " sidebar__link--active" : ""
            }`}
            aria-current={active === item.key ? "page" : undefined}
            onClick={() => onNavigate(item.key)}
            data-testid={`nav-${item.key}`}
          >
            <span className="sidebar__link-label">{item.label}</span>
            <span className="sidebar__link-hint">{item.hint}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar__footer">
        Razorpay Buildathon · Track 03 · Revenue Recovery
      </div>
    </aside>
  );
}
