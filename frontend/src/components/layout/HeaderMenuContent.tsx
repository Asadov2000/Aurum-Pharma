import { Link } from "@tanstack/react-router";
import { useConnectivity } from "@/lib/connectivityContext";
import { NavIcon } from "./icons";
import { PwaInstallButton } from "./PwaInstallButton";
import { RuntimeSurfaceBadge } from "./RuntimeSurfaceBadge";

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
      <div className="border-b border-border px-4 py-3">
        <p className="break-words text-sm font-semibold text-foreground">{name}</p>
        <p className="text-xs text-foreground-muted">{caption}</p>
      </div>
      <div className="space-y-0.5 p-2">
        <ProfileLink to="/profile" label="Мой профиль" close={close} />
        {canOpenMyWork ? <ProfileLink to="/my-work" label="Моя работа" close={close} /> : null}
        <ProfileLink to="/security" label="Безопасность" close={close} />
        <ProfileLink to="/notifications" label="Уведомления" close={close} />
        <ProfileLink to="/settings" label="Внешний вид и настройки" close={close} />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3">
        <RuntimeSurfaceBadge className="inline-flex" />
        <PwaInstallButton />
      </div>
      <div className="border-t border-border p-2">
        <button
          type="button"
          className="flex min-h-11 w-full items-center gap-3 rounded-md px-3 text-sm text-danger hover:bg-danger/5"
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
        ? "Нет интернет-соединения. Действия, которым нужен сервер, могут быть недоступны."
        : status === "server-unavailable"
          ? "Сервис недоступен. Проверьте соединение или повторите проверку позже."
          : "Проверяем соединение с сервером…";
  return (
    <div className="space-y-3 p-4">
      <p className="text-sm text-foreground-secondary">{description}</p>
      <p className="text-xs text-foreground-muted">
        Это состояние соединения. Оно не подтверждает синхронизацию данных.
      </p>
      <button
        type="button"
        disabled={status === "checking"}
        className="min-h-11 rounded-md border border-border px-3 text-sm font-medium text-primary hover:bg-primary/5 disabled:opacity-50"
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

function ProfileLink({
  to,
  label,
  close,
}: {
  to: "/profile" | "/my-work" | "/security" | "/notifications" | "/settings";
  label: string;
  close: () => void;
}): JSX.Element {
  return (
    <Link
      to={to}
      onClick={close}
      className="flex min-h-11 items-center gap-3 rounded-md px-3 text-sm text-foreground-secondary hover:bg-primary/5 hover:text-primary"
    >
      <NavIcon to={to} />
      {label}
    </Link>
  );
}

function LogoutIcon(): JSX.Element {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
    </svg>
  );
}
