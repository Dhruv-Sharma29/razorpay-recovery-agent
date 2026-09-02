/**
 * TopBar component.
 *
 * Purely presentational brand header for the PayPulse console.
 * Displays a "TEST MODE" badge so it is always clear that no
 * live money is being moved — no policy or pipeline logic here.
 */

export default function TopBar() {
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
        <span className="topbar__name">PayPulse</span>
        <span className="topbar__tagline">Failed-Payment Recovery Console</span>
      </div>
      <span className="badge badge--test" data-testid="test-mode-badge">
        Test Mode
      </span>
    </div>
  );
}
