import { computed, ref } from 'vue'
import zhCN from '@/locales/zh-CN'
import en from '@/locales/en'

export type Locale = 'zh-CN' | 'en'
type Widen<T> = T extends string ? string : T extends readonly string[] ? readonly string[] : { [K in keyof T]: Widen<T[K]> }
type Messages = Widen<typeof zhCN>

const LOCALE_STORAGE_KEY = 'afterglow.locale'

function detectLocale(): Locale {
  if (typeof navigator === 'undefined') return 'zh-CN'
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language]
  return languages.some((language) => language?.toLowerCase().startsWith('zh')) ? 'zh-CN' : 'en'
}

function loadLocale(): Locale {
  if (typeof localStorage !== 'undefined') {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY)
    if (stored === 'zh-CN' || stored === 'en') return stored
  }
  return detectLocale()
}

const currentLocale = ref<Locale>(loadLocale())
const messages: Record<Locale, Messages> = { 'zh-CN': zhCN, en }

function getValue(source: unknown, path: string): unknown {
  return path.split('.').reduce((value, key) => (value && typeof value === 'object' ? (value as Record<string, unknown>)[key] : undefined), source)
}

export function useLocale() {
  const isChinese = computed(() => currentLocale.value === 'zh-CN')
  function t(path: string, params: Record<string, string | number> = {}) {
    const value = getValue(messages[currentLocale.value], path) ?? getValue(messages.en, path) ?? path
    return String(value).replace(/\{(\w+)\}/g, (_, key: string) => String(params[key] ?? `{${key}}`))
  }
  function list(path: string): string[] {
    const value = getValue(messages[currentLocale.value], path) ?? getValue(messages.en, path)
    return Array.isArray(value) ? value.map(String) : []
  }
  return { locale: currentLocale, isChinese, t, list }
}

export function setLocale(locale: Locale) {
  currentLocale.value = locale
  if (typeof localStorage !== 'undefined') localStorage.setItem(LOCALE_STORAGE_KEY, locale)
}

