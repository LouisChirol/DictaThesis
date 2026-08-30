/** Shared theme switcher — loaded before hud.js / settings.js (global script scope). */

function applyUiTheme(value: string | undefined | null): void {
  document.documentElement.dataset.theme = value === "parchment" ? "parchment" : "mocha";
}
