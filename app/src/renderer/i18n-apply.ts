/** Apply translated strings to HUD / settings DOM (uses window.i18n from preload). */

function applyI18nText(): void {
  document.querySelectorAll<HTMLElement>("[data-i18n]").forEach((el) => {
    const key = el.dataset.i18n;
    if (key) el.textContent = window.i18n.t(key);
  });
}

function applyI18nTitles(): void {
  document.querySelectorAll<HTMLElement>("[data-i18n-title]").forEach((el) => {
    const key = el.dataset.i18nTitle;
    if (key) el.title = window.i18n.t(key);
  });
}

function applyI18nPlaceholders(): void {
  document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("[data-i18n-placeholder]").forEach(
    (el) => {
      const key = el.dataset.i18nPlaceholder;
      if (key) el.placeholder = window.i18n.t(key);
    },
  );
}

function applyI18nLabels(): void {
  document.querySelectorAll<HTMLElement>("[data-i18n-label]").forEach((el) => {
    const key = el.dataset.i18nLabel;
    if (!key) return;
    const text = window.i18n.t(key);
    if (el.tagName === "OPTION") {
      (el as HTMLOptionElement).textContent = text;
    } else {
      el.textContent = text;
    }
  });
}

function applyDocumentLocale(): void {
  document.documentElement.lang = window.i18n.locale;
}

function applyHudI18n(): void {
  applyDocumentLocale();
  applyI18nText();
  applyI18nTitles();
}

function applySettingsI18n(): void {
  applyDocumentLocale();
  applyI18nText();
  applyI18nTitles();
  applyI18nPlaceholders();
  applyI18nLabels();

  const titleKey = "settings.title";
  document.title = window.i18n.t(titleKey);
}
