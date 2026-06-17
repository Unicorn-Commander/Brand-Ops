/**
 * Ambient-effects preference (grain, auras, glows) applied to <body>.
 *
 * This lives in its own leaf module on purpose. The eager Layout calls
 * applyAmbient() on boot, and SettingsPage reads/toggles the same preference.
 * If these helpers stayed on SettingsPage, the eager Layout's static import
 * would pull the entire (otherwise lazy) settings page into the entry chunk.
 * Isolated here, both importers share three tiny functions and SettingsPage
 * code-splits cleanly.
 */
const AMBIENT_KEY = "brandops.ambient";

/** Read the stored ambient preference; ambient is on unless explicitly off. */
export function ambientEnabled(): boolean {
  if (typeof localStorage === "undefined") return true;
  return localStorage.getItem(AMBIENT_KEY) !== "off";
}

/** Persist the ambient preference. */
export function setAmbientEnabled(on: boolean): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(AMBIENT_KEY, on ? "on" : "off");
}

/** Apply the stored ambient preference to <body> (called on boot and toggle). */
export function applyAmbient(): void {
  if (typeof document === "undefined") return;
  document.body.classList.toggle("no-ambient", !ambientEnabled());
}
