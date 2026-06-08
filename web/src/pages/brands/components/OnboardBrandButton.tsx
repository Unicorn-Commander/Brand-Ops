import { Sparkles } from "lucide-react";
import { Button } from "../../../components/Button";

/**
 * Placeholder for onboarding a new brand. This is where the autonomous
 * BrandResearch flow will live: point it at a name or site and it researches
 * the brand, drafts the identity (colors, fonts, voice, references), and seeds
 * it. That flow is not built yet, so the button is disabled and explains itself
 * on hover. Wrapping the disabled button in a titled span keeps the native
 * tooltip working (a disabled button does not fire hover events on its own).
 */
export function OnboardBrandButton() {
  return (
    <span
      className="inline-flex cursor-not-allowed"
      title="Coming soon: BrandResearch researches a brand from its name or site and drafts its identity for you. Not available yet."
    >
      <Button
        variant="primary"
        size="sm"
        disabled
        icon={<Sparkles size={15} />}
      >
        Onboard a new brand
      </Button>
    </span>
  );
}
