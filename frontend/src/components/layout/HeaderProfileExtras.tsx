import { PwaInstallButton } from "./PwaInstallButton";
import { RuntimeSurfaceBadge } from "./RuntimeSurfaceBadge";

export default function HeaderProfileExtras(): JSX.Element {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3">
      <RuntimeSurfaceBadge className="inline-flex" />
      <PwaInstallButton />
    </div>
  );
}
