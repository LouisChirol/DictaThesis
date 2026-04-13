/**
 * Settings renderer — form logic, save/load, and .bib file loading.
 */

const apiKeyInput = document.getElementById("api-key") as HTMLInputElement;
const apiKeyToggle = document.getElementById("api-key-toggle") as HTMLButtonElement;
const shortcutInput = document.getElementById("shortcut-key") as HTMLInputElement;
const vadSlider = document.getElementById("vad-silence") as HTMLInputElement;
const vadValue = document.getElementById("vad-silence-value") as HTMLSpanElement;
const vocabularyArea = document.getElementById("vocabulary") as HTMLTextAreaElement;
const bibliographyArea = document.getElementById("bibliography") as HTMLTextAreaElement;
const btnLoadBib = document.getElementById("btn-load-bib") as HTMLButtonElement;
const btnSave = document.getElementById("btn-save") as HTMLButtonElement;
const toast = document.getElementById("toast") as HTMLDivElement;

// ── API key toggle ──

apiKeyToggle.addEventListener("click", () => {
  const isPassword = apiKeyInput.type === "password";
  apiKeyInput.type = isPassword ? "text" : "password";
  apiKeyToggle.textContent = isPassword ? "Hide" : "Show";
});

// ── Slider live value ──

vadSlider.addEventListener("input", () => {
  vadValue.textContent = `${vadSlider.value}s`;
});

// ── Load .bib file ──

btnLoadBib.addEventListener("click", async () => {
  const content = await window.dictaThesis.loadBibFile();
  if (content) {
    bibliographyArea.value = content;
  }
});

// ── Save ──

btnSave.addEventListener("click", () => {
  const languageRadio = document.querySelector(
    'input[name="language"]:checked'
  ) as HTMLInputElement | null;

  const data: Record<string, unknown> = {
    api_key: apiKeyInput.value,
    language: languageRadio?.value || "fr",
    shortcut_key: shortcutInput.value.toLowerCase().trim() || "f9",
    vad_silence_duration: parseFloat(vadSlider.value),
    vocabulary: vocabularyArea.value
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean),
    bibliography: bibliographyArea.value,
  };

  window.dictaThesis.saveSettings(data);
  showToast("Settings saved");
});

// ── Toast ──

function showToast(message: string): void {
  toast.textContent = message;
  toast.classList.add("visible");
  setTimeout(() => toast.classList.remove("visible"), 2000);
}

// ── Populate form when settings arrive ──

window.dictaThesis.onSettings((data) => {
  const s = data.data;

  apiKeyInput.value = s.api_key || "";

  const langRadio = document.querySelector(
    `input[name="language"][value="${s.language}"]`
  ) as HTMLInputElement | null;
  if (langRadio) langRadio.checked = true;

  shortcutInput.value = s.shortcut_key || "f9";

  vadSlider.value = String(s.vad_silence_duration ?? 1.5);
  vadValue.textContent = `${vadSlider.value}s`;

  vocabularyArea.value = (s.vocabulary || []).join("\n");
  bibliographyArea.value = s.bibliography || "";
});

// Request settings on load
window.dictaThesis.getSettings();
