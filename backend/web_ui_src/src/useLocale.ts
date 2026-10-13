import { computed, ref } from 'vue'
import zhCN from './locales/zh-CN'
import en from './locales/en'

export type Locale = 'zh-CN' | 'en'
const locale = ref<Locale>(detectLocale())
const messages = { 'zh-CN': zhCN, en }
const STORAGE_KEY = 'afterglow.config.locale'

if (typeof localStorage !== 'undefined') {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'zh-CN' || stored === 'en') locale.value = stored
}

function detectLocale(): Locale {
  if (typeof navigator === 'undefined') return 'zh-CN'
  const values = navigator.languages?.length ? navigator.languages : [navigator.language]
  return values.some((value) => value?.toLowerCase().startsWith('zh')) ? 'zh-CN' : 'en'
}

export function useLocale() {
  const isChinese = computed(() => locale.value === 'zh-CN')
  function t(key: keyof typeof zhCN) {
    return messages[locale.value][key] ?? messages.en[key] ?? key
  }
  function setLocale(value: Locale) {
    locale.value = value
    if (typeof localStorage !== 'undefined') localStorage.setItem(STORAGE_KEY, value)
  }
  return { locale, isChinese, t, setLocale }
}
