import { computed, ref } from 'vue'
import zhCN from './locales/zh-CN'
import en from './locales/en'

export type Locale = 'zh-CN' | 'en'
const locale = ref<Locale>(detectLocale())
const messages = { 'zh-CN': zhCN, en }
const STORAGE_KEY = 'afterglow.config.locale'
const FALLBACK_TEXT: Record<Locale, string> = {
  'zh-CN': '文案暂缺',
  en: 'Text unavailable',
}

try {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'zh-CN' || stored === 'en') locale.value = stored
} catch { /* Storage can be disabled by the browser. */ }

function detectLocale(): Locale {
  if (typeof navigator === 'undefined') return 'en'
  const values = navigator.languages?.length ? navigator.languages : [navigator.language]
  return values.some((value) => value?.toLowerCase().startsWith('zh')) ? 'zh-CN' : 'en'
}

export function useLocale() {
  const isChinese = computed(() => locale.value === 'zh-CN')
  function t(key: keyof typeof zhCN, params: Record<string, string | number> = {}) {
    const text = messages[locale.value][key] ?? messages.en[key] ?? FALLBACK_TEXT[locale.value]
    return text.replace(/\{(\w+)\}/g, (_, name: string) => String(params[name] ?? `{${name}}`))
  }
  function setLocale(value: Locale) {
    locale.value = value
    try { localStorage.setItem(STORAGE_KEY, value) } catch { /* Keep the session selection. */ }
  }
  return { locale, isChinese, t, setLocale }
}
