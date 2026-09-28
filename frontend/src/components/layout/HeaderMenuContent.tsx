import { Link } from "@tanstack/react-router";
import { useConnectivity } from "@/lib/connectivityContext";
import HeaderProfileExtras from "./HeaderProfileExtras";
import { HeaderLineIcon, NavIcon } from "./icons";

const PROFILE_LINKS = [
  ["/profile", "Мой профиль"],
  ["/my-work", "Моя работа"],
  ["/security", "Безопасность"],
  ["/notifications", "Уведомления"],
  ["/settings", "Внешний вид и настройки"],
] as const;

type HeaderMenuContentProps =
  | { kind: "connection"; close: () => void }
  | {
      kind: "profile";
      name: string;
      caption: string;
      canOpenMyWork: boolean;
      onLogout: () => void;
      close: () => void;
    };

export default function HeaderMenuContent(props: HeaderMenuContentProps): JSX.Element {
  if (props.kind === "connection") return <ConnectionDetails close={props.close} />;
  const { name, caption, canOpenMyWork, onLogout, close } = props;
  return (
    <>
      <div className="app-header-profile-details">
        <p className="app-header-profile-name">{name}</p>
        <p className="app-header-note">{caption}</p>
      </div>
      <div className="app-header-profile-links">
        {PROFILE_LINKS.filter(([to]) => to !== "/my-work" || canOpenMyWork).map(([to, label]) => (
          <Link key={to} to={to} onClick={close} className="app-header-profile-link">
            <NavIcon to={to} />
            {label}
          </Link>
        ))}
      </div>
      <HeaderProfileExtras />
      <div className="app-header-logout-row">
        <button
          type="button"
          className="app-header-logout"
          onClick={() => {
            close();
            onLogout();
          }}
        >
          <LogoutIcon />
          Выйти
        </button>
      </div>
    </>
  );
}

function ConnectionDetails({ close }: { close: () => void }): JSX.Element {
  const { status, checkNow } = useConnectivity();
  const description =
    status === "online"
      ? "Соединение с сервером есть."
      : status === "offline"
        ? "Нет интернета. Действия, которым нужен сервер, могут быть недоступны."
        : status === "server-unavailable"
          ? "Сервис недоступен. Повторите проверку позже."
          : "Проверяем соединение с сервером…";
  return (
    <div className="space-y-3 p-4">
      <p className="app-header-detail">{description}</p>
      <p className="app-header-note">Статус сети не подтверждает синхронизацию.</p>
      <button
        type="button"
        disabled={status === "checking"}
        className="app-header-check-connection"
        onClick={() => {
          checkNow();
          close();
        }}
      >
        Проверить соединение
      </button>
    </div>
  );
}

function LogoutIcon(): JSX.Element {
  return (
    <HeaderLineIcon>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
    </HeaderLineIcon>
  );
}
