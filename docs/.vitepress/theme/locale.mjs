export const storageKey = 'afterglow.docs.locale'

export function preferredLocale(stored, languages = []) {
  if (stored === 'zh' || stored === 'en') return stored
  return languages.some(language => /^zh(?:-|$)/i.test(language)) ? 'zh' : 'en'
}

// Only translate paired section anchors; headings without a counterpart go to the article.
export function localeTarget(pathname, hash, locale, base, sections) {
  const relative = pathname.startsWith(base) ? pathname.slice(base.length) : pathname.replace(/^\//, '')
  const match = relative.match(/^(en|zh)\/(.*)$/)
  if (!match) return `${base}${locale}/`
  const slug = match[2].replace(/\.html$/, '').replace(/\/$/, '') || 'index'
  const target = `${base}${locale}/${slug === 'index' ? '' : `${slug}.html`}`
  let anchor
  try { anchor = decodeURIComponent(hash.replace(/^#/, '')) } catch { anchor = '' }
  const section = sections[slug]?.find(pair => pair[match[1]] === anchor)
  return target + (section ? `#${encodeURIComponent(section[locale])}` : '')
}
