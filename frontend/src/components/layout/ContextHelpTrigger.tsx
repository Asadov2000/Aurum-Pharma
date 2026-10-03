import { ContextHelp, type HelpLocation } from "./ContextHelp";
import { HeaderPopover } from "./AppHeader";

export function ContextHelpTrigger({ pathname, operationKind }: HelpLocation): JSX.Element {
  return (
    <HeaderPopover
      key={`help:${pathname}:${operationKind ?? ""}`}
      title="Помощь по разделу"
      triggerClassName="app-header-settings flex w-auto items-center gap-2 px-2"
      trigger={
        <>
          <span
            aria-hidden="true"
            className="grid h-5 w-5 place-items-center rounded-full border-2 border-current text-xs font-bold"
          >
            ?
          </span>
          <span className="hidden text-xs font-medium xl:inline">Помощь</span>
        </>
      }
    >
      {(close) => <ContextHelp pathname={pathname} operationKind={operationKind} close={close} />}
    </HeaderPopover>
  );
}
