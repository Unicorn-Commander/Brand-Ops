import { AssetThumb } from "../../../components/AssetThumb";
import type { AssetGroup } from "./catalog";

interface AssetGridProps {
  groups: AssetGroup[];
  /** key of the open group, for the selection ring. */
  selectedKey: string | null;
  onSelect: (group: AssetGroup) => void;
}

/**
 * A responsive grid of asset thumbnails, one card per logical asset (its
 * current version). A version count rides in the corner when an asset has more
 * than one render. Clicking opens the detail drawer for that asset.
 *
 * The API serves only file basenames, not bytes, so AssetThumb renders its
 * labeled placeholder; once a static-file route exists a `src` could be threaded
 * through here.
 */
export function AssetGrid({ groups, selectedKey, onSelect }: AssetGridProps) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
      {groups.map((group) => {
        const selected = group.key === selectedKey;
        const versionCount = group.versions.length;
        return (
          <div key={group.key} className="relative">
            <AssetThumb
              asset={group.current}
              src={group.current.url ?? undefined}
              onClick={() => onSelect(group)}
              className={
                selected ? "ring-2 ring-accent ring-offset-2 ring-offset-chrome-bg rounded-lg" : ""
              }
            />
            {versionCount > 1 && (
              <span
                className="pointer-events-none absolute right-1.5 top-1.5 rounded-md bg-chrome-bg/80 px-1.5 py-0.5 text-[11px] font-medium text-text-muted ring-1 ring-chrome-border backdrop-blur"
                title={`${versionCount} versions`}
              >
                v{versionCount}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
