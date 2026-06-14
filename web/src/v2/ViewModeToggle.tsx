import { motion } from "framer-motion";
import { spring } from "../lib/motion";
import { useViewMode, type ViewMode } from "../view-mode";

const OPTIONS: { id: ViewMode; label: string; hint: string }[] = [
  { id: "simple", label: "Simple", hint: "Guided, plain-language" },
  { id: "advanced", label: "Advanced", hint: "Full controls and technical detail" },
];

/**
 * The Simple/Advanced preference switch for the top bar. A segmented control
 * whose active pill glides between options via a shared layoutId (the Linear
 * move, same technique as ViewToggle), styled to the v2 chrome so it sits with
 * the topbar's other controls. Text labels, not icons, since the distinction is
 * conceptual and must be unambiguous.
 */
export function ViewModeToggle() {
  const { mode, setMode } = useViewMode();
  return (
    <div className="vm-toggle" role="tablist" aria-label="Detail level">
      {OPTIONS.map((o) => {
        const active = mode === o.id;
        return (
          <button
            key={o.id}
            type="button"
            role="tab"
            aria-selected={active}
            title={o.hint}
            className={`vm-toggle-opt${active ? " on" : ""}`}
            onClick={() => setMode(o.id)}
          >
            {active && (
              <motion.span
                layoutId="vm-toggle-pill"
                className="vm-toggle-pill"
                transition={spring.snap}
              />
            )}
            <span className="vm-toggle-label">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}
