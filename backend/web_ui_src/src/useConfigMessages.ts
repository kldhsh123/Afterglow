import zhCN from './locales/zh-CN'
import en from './locales/en'
import { useLocale } from './useLocale'

// Match only known UI messages; filenames, provider diagnostics and user data stay intact.
const matchers = [zhCN, en].flatMap(catalog => Object.entries(catalog)
  .filter(([key]) => key.startsWith('backend'))
  .map(([key, template]) => {
    const names: string[] = []
    const pattern = template.split(/(\{\w+\})/g).map(part => {
      if (/^\{\w+\}$/.test(part)) {
        names.push(part.slice(1, -1))
        return '([\\s\\S]*?)'
      }
      return part.replace(/[.*+?^$(){}|[\]\\]/g, '\\$&')
    }).join('')
    return { key: key as keyof typeof zhCN, names, pattern: new RegExp('^' + pattern + '$') }
  }))

export function useConfigMessages() {
  const { t } = useLocale()
  function translateBackendText(text: string): string {
    if (!text) return text
    for (const hint of [zhCN.backendEmbeddingSchemaHint, en.backendEmbeddingSchemaHint]) {
      if (text.endsWith(hint)) {
        return translateBackendText(text.slice(0, -hint.length)) + t('backendEmbeddingSchemaHint')
      }
    }
    for (const { key, names, pattern } of matchers) {
      const match = pattern.exec(text)
      if (match) {
        const params: Record<string, string> = {}
        names.forEach((name, index) => { params[name] = match[index + 1] ?? '' })
        return t(key, params)
      }
    }
    if (text.includes(' · ')) return text.split(' · ').map(translateBackendText).join(' · ')
    if (text.includes('\n')) return text.split('\n').map(translateBackendText).join('\n')
    const colon = text.indexOf('：')
    if (colon > 0) {
      const prefix = text.slice(0, colon + 1)
      const suffix = translateBackendText(text.slice(colon + 1))
      if (suffix !== text.slice(colon + 1)) return prefix + suffix
    }
    return text
  }
  return { translateBackendText }
}
