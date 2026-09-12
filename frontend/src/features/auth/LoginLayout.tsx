import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

export function LoginLayout({
  children,
  publicWebsiteUrl,
  wide = false,
}: {
  children: ReactNode;
  publicWebsiteUrl: string | null;
  wide?: boolean;
}): JSX.Element {
  return (
    <main className="min-h-[100dvh] bg-background lg:grid lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:bg-surface">
      <aside
        className="relative m-[18px] hidden min-h-[calc(100dvh-36px)] flex-col justify-end overflow-hidden rounded-[18px] bg-[#164942] bg-[url('/assets/auth-pharmacy.webp')] bg-cover bg-center p-9 lg:flex"
        aria-hidden="true"
      >
        <div className="absolute inset-0 bg-gradient-to-b from-transparent from-25% to-[#062f37]/95" />
        <div className="absolute left-7 top-7 rounded-lg bg-[#00566b]/85 px-[15px] py-[13px]">
          <img
            src="/brand/v12-8/logo-white.svg"
            alt=""
            width="1061"
            height="256"
            className="w-[170px]"
          />
        </div>
        <div className="relative max-w-[395px] pt-28 text-white">
          <p className="mb-5 text-xs font-semibold uppercase tracking-[0.14em] text-white/90">
            Рабочее пространство аптеки
          </p>
          <h2 className="font-display text-[42px] font-semibold leading-[1.1] tracking-[-0.045em]">
            Каждый день.
            <br />
            <span className="text-[#80d9d1]">Всё под рукой.</span>
          </h2>
          <p className="mt-6 max-w-[340px] text-[15px] leading-7 text-white/90">
            Продажи, товары и команда — в одном приложении.
          </p>
          <p className="mt-9 text-xs leading-5 text-white/80">Для аптек Таджикистана · TJS</p>
        </div>
      </aside>

      <section className="flex min-h-[100dvh] min-w-0 flex-col">
        <span className="sr-only">Aurum Pharma</span>
        <div className="flex min-h-20 items-center justify-between gap-4 px-[22px] py-[18px] sm:px-10 sm:py-6 lg:justify-end">
          <LoginBrand className="w-[155px] sm:w-[170px] lg:hidden" />
          {publicWebsiteUrl ? (
            <a
              href={publicWebsiteUrl}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="На сайт — О проекте Aurum Pharma (откроется в новой вкладке)"
              className="inline-flex min-h-11 items-center gap-2 rounded-md px-1 text-sm text-foreground-secondary transition-colors hover:text-primary motion-reduce:transition-none"
            >
              <span aria-hidden="true">←</span>
              На сайт
            </a>
          ) : null}
        </div>

        <div className="flex flex-1 items-start justify-center px-[18px] pb-12 pt-4 sm:items-center sm:px-8 sm:pb-16 sm:pt-6 lg:px-12">
          <div
            className={cn(
              "w-full min-w-0 rounded-[14px] border border-border bg-surface p-6 sm:p-7 lg:rounded-none lg:border-0 lg:p-0",
              wide ? "max-w-lg" : "max-w-[400px]",
            )}
          >
            <LoginBrand className="hidden w-[200px] lg:inline-flex" />
            <div className="lg:mt-12 [&_input:not([type=checkbox]):not([type=radio])]:min-h-[54px] [&_input:not([type=checkbox]):not([type=radio])]:text-base [&_button[type=submit]]:min-h-[52px]">
              {children}
            </div>
          </div>
        </div>

        <p className="px-6 pb-[max(24px,env(safe-area-inset-bottom))] text-center text-xs leading-5 text-foreground-muted">
          Рабочее пространство вашей аптеки
        </p>
      </section>
    </main>
  );
}

function LoginBrand({ className }: { className: string }): JSX.Element {
  return (
    <span
      className={cn("brand-mark inline-flex min-w-0 max-w-full items-center", className)}
      data-brand-tone="default"
      aria-hidden="true"
    >
      <img
        src="/brand/v12-8/logo-color.svg"
        alt=""
        width="1061"
        height="256"
        className="brand-mark-color w-full"
      />
      <img
        src="/brand/v12-8/logo-white.svg"
        alt=""
        width="1061"
        height="256"
        className="brand-mark-white w-full"
      />
    </span>
  );
}
