import { en } from "./en";
import { fr } from "./fr";

export type MessageKey = keyof typeof en;
export type UiLocale = "en" | "fr";

const MESSAGES: Record<UiLocale, Record<MessageKey, string>> = { en, fr };

/** Dictation language setting → UI locale (auto defaults to English). */
export function uiLocaleFromSetting(language: string | undefined | null): UiLocale {
  return language === "fr" ? "fr" : "en";
}

export function t(key: MessageKey, locale: UiLocale): string {
  return MESSAGES[locale][key] ?? MESSAGES.en[key] ?? key;
}

/** Map known backend error strings to translation keys. */
const ERROR_KEYS: Record<string, MessageKey> = {
  "No API key configured — open Settings first": "error.noApiKey",
};

export function localizeBackendMessage(message: string, locale: UiLocale): string {
  const key = ERROR_KEYS[message];
  return key ? t(key, locale) : message;
}
