import sidebarIcons from "@/assets/sidebar-icons.svg";

// Approved local vectors; original licenses: /licenses/sidebar-icons.html.
// One cached sprite keeps SVG paths out of the startup JavaScript bundle.
const ICONS: Record<string, string> = {
  "/": "nav-0",
  "/onboarding": "nav-1",
  "/pos": "nav-2",
  "/sales": "nav-3",
  "/payment-reconciliation": "nav-4",
  "/catalog": "nav-5",
  "/batches": "nav-6",
  "/incoming": "nav-7",
  "/suppliers": "nav-8",
  "/reports": "nav-9",
  "/audit": "nav-10",
  "/my-work": "nav-11",
  "/users": "nav-12",
  "/staff-pay": "nav-13",
  "/roles": "nav-14",
  "/branches": "nav-15",
  "/registers": "nav-16",
  "/notifications": "nav-17",
  "/security": "nav-18",
  "/settings": "nav-19",
  "/admin": "nav-20",
  menu: "nav-21",
  collapse: "nav-22",
};

export function NavIcon({ to }: { to: string }): JSX.Element {
  const icon = ICONS[to];
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
      className="shrink-0"
    >
      {icon ? (
        <use href={sidebarIcons + "#" + icon} width="20" height="20" />
      ) : (
        <circle cx="10" cy="10" r="3" />
      )}
    </svg>
  );
}

export function NavigationSettingsIcon(): JSX.Element {
  return <NavIcon to="menu" />;
}

export function ExpandNavigationIcon({ expanded }: { expanded: boolean }): JSX.Element {
  return (
    <span
      className="inline-flex shrink-0"
      style={expanded ? undefined : { transform: "rotate(180deg)" }}
    >
      <NavIcon to="collapse" />
    </span>
  );
}
