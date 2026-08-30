/** i18n bridge exposed from preload. */

type I18nKey = string;

interface Window {
  i18n: {
    locale: "en" | "fr";
    t(key: I18nKey): string;
    localizeMessage(message: string): string;
    setLocale(language: string): void;
    onLocaleChange(cb: (locale: "en" | "fr") => void): void;
  };
  applyHudI18n(): void;
  applySettingsI18n(): void;
}
