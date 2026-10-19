import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const docs = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const dist = resolve(docs, '.vitepress/dist')
const pages = JSON.parse(readFileSync(resolve(docs, 'pages.json'), 'utf8'))
const base = process.env.DOCS_BASE || '/Afterglow/'
const htmlCache = new Map()
function htmlFor(file) {
  if (!htmlCache.has(file)) htmlCache.set(file, readFileSync(file, 'utf8'))
  return htmlCache.get(file)
}
let checked = 0
for (const locale of ['en', 'zh']) {
  for (const page of pages) {
    const file = resolve(dist, locale, `${page.slug}.html`)
    assert.ok(existsSync(file), `Missing output: ${file}`)
    const html = htmlFor(file)
    assert.ok(!html.includes('/edit/main/docs/undefined'), `${file}: broken edit link`)
    assert.ok(html.includes(`<html lang="${locale === 'zh' ? 'zh-CN' : 'en'}"`), `${file}: wrong page language`)
    for (const match of html.matchAll(/<a\b[^>]*\bhref="([^"]+)"/g)) {
      const raw = match[1].replaceAll('&amp;', '&')
      if (/^(?:https?:|mailto:|tel:|javascript:)/.test(raw)) continue
      const url = new URL(raw, `https://docs.example${base}${locale}/${page.slug}.html`)
      if (!url.pathname.startsWith(base)) continue
      let relative = decodeURIComponent(url.pathname.slice(base.length))
      if (!relative || relative.endsWith('/')) relative += 'index.html'
      if (!relative.match(/\.[a-z]+$/)) relative += '.html'
      const target = resolve(dist, relative)
      assert.ok(target.startsWith(dist + '/'), 'Link escapes dist')
      assert.ok(existsSync(target), `${file}: missing link ${raw}`)
      if (url.hash && target.endsWith('.html')) {
        const id = decodeURIComponent(url.hash.slice(1))
        const targetHtml = htmlFor(target)
        assert.ok(targetHtml.includes(`id="${id}"`), `${file}: missing anchor ${raw}`)
      }
      checked++
    }
  }
}
for (const lang of ['en', 'zh']) {
  assert.ok(existsSync(resolve(dist, 'assets', 'chunks')), 'Missing bundled assets')
  // Locale-specific indexes prevent searches from sending readers to the other language.
  const index = readFileSync(resolve(dist, 'hashmap.json'), 'utf8')
  assert.ok(index.includes(`${lang}_index.md`), `Missing locale output in manifest: ${lang}`)
}
console.log(`Checked ${pages.length * 2} localized pages and ${checked} rendered internal links/anchors.`)
