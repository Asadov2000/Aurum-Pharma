import { type ReactNode } from "react";

/** Tiny inline stroke icons for the sidebar. Kept local (no icon dependency,
 *  matching the project's deliberate "no icon lib" choice) and uniform: 18px,
 *  currentColor, aria-hidden so the link's accessible name stays its label. */
function Svg({ children }: { children: ReactNode }): JSX.Element {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      {children}
    </svg>
  );
}

const ICONS: Record<string, ReactNode> = {
  // Главная — home
  "/": (
    <Svg>
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V21h14V9.5" />
    </Svg>
  ),
  // Касса — point of sale / scan
  "/pos": (
    <Svg>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M7 8h.01M7 12h6M7 16h10" />
    </Svg>
  ),
  // Чеки — receipt
  "/sales": (
    <Svg>
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" />
      <path d="M9 8h6M9 12h6" />
    </Svg>
  ),
  // Каталог — capsule / medication
  "/catalog": (
    <Svg>
      <rect x="3" y="8" width="18" height="8" rx="4" />
      <path d="M12 8v8" />
    </Svg>
  ),
  // Партии — stacked layers / batches
  "/batches": (
    <Svg>
      <path d="m12 3 9 5-9 5-9-5 9-5Z" />
      <path d="m3 13 9 5 9-5" />
    </Svg>
  ),
  // Приходы — incoming box (arrow in)
  "/incoming": (
    <Svg>
      <path d="M21 16V8l-9-5-9 5v8l9 5 9-5Z" />
      <path d="M12 11v6M9 14l3 3 3-3" />
    </Svg>
  ),
  // Поставщики — building / warehouse
  "/suppliers": (
    <Svg>
      <path d="M3 21V8l9-5 9 5v13" />
      <path d="M9 21v-6h6v6M3 21h18" />
    </Svg>
  ),
  // Отчёты — bar chart
  "/reports": (
    <Svg>
      <path d="M4 20h16" />
      <path d="M7 20v-6M12 20V8M17 20v-9" />
    </Svg>
  ),
  // Аудит — checklist / log
  "/audit": (
    <Svg>
      <path d="M9 5h9M9 12h9M9 19h9" />
      <path d="m3 5 1.5 1.5L7 4M3 12l1.5 1.5L7 11M3 19l1.5 1.5L7 18" />
    </Svg>
  ),
  // Пользователи — people
  "/users": (
    <Svg>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0M16 6.2a3.2 3.2 0 0 1 0 6M18 20a5.5 5.5 0 0 0-3-4.9" />
    </Svg>
  ),
  // Роли — key
  "/roles": (
    <Svg>
      <circle cx="8" cy="8" r="4" />
      <path d="m11 11 8 8M16 16l2-2M18 18l2-2" />
    </Svg>
  ),
  // Точки — store location
  "/branches": (
    <Svg>
      <path d="M12 21s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12Z" />
      <circle cx="12" cy="9" r="2.5" />
    </Svg>
  ),
  // Кассы — register / monitor
  "/registers": (
    <Svg>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </Svg>
  ),
  // Биллинг — credit card
  "/billing": (
    <Svg>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 10h18M7 15h4" />
    </Svg>
  ),
  // Уведомления — bell
  "/notifications": (
    <Svg>
      <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M10.5 21a2 2 0 0 0 3 0" />
    </Svg>
  ),
  // Безопасность — ключ / account access
  "/security": (
    <Svg>
      <circle cx="8" cy="15" r="3" />
      <path d="m10.2 12.8 8.3-8.3M15 7l2 2M17 5l2 2" />
    </Svg>
  ),
  // Настройки — gear
  "/settings": (
    <Svg>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
    </Svg>
  ),
  // Старт — onboarding / flag
  "/onboarding": (
    <Svg>
      <path d="M5 21V4M5 4h11l-2 4 2 4H5" />
    </Svg>
  ),
  // Тенанты — buildings / org
  "/admin/tenants": (
    <Svg>
      <rect x="3" y="3" width="8" height="18" rx="1" />
      <rect x="13" y="8" width="8" height="13" rx="1" />
      <path d="M6 7h2M6 11h2M6 15h2M16 12h2M16 16h2" />
    </Svg>
  ),
};

/** A neutral dot fallback so an unmapped route still aligns with the others. */
const FALLBACK: ReactNode = (
  <Svg>
    <circle cx="12" cy="12" r="3.5" />
  </Svg>
);

/** Resolve a sidebar icon by route. A component (not a helper fn) so the file
 *  only exports components — keeps react-refresh happy. */
export function NavIcon({ to }: { to: string }): JSX.Element {
  return <>{ICONS[to] ?? FALLBACK}</>;
}
