import test from 'node:test'
import assert from 'node:assert/strict'
import { preferredLocale, localeTarget } from '../.vitepress/theme/locale.mjs'

test('English default, Simplified and Traditional Chinese detection', () => {
  assert.equal(preferredLocale(undefined, []), 'en')
  for (const languages of [['en-US'], ['fr-FR', 'ja-JP'], ['zhx']]) assert.equal(preferredLocale(undefined, languages), 'en')
  for (const language of ['zh', 'zh-CN', 'zh-TW', 'zh-HK', 'zh-Hans', 'zh-Hant-TW', 'ZH-hant']) {
    assert.equal(preferredLocale(undefined, ['en-US', language]), 'zh')
  }
})
test('manual choice overrides browser preferences; invalid storage is ignored', () => {
  assert.equal(preferredLocale('en', ['zh-TW']), 'en')
  assert.equal(preferredLocale('zh', ['en-US']), 'zh')
  assert.equal(preferredLocale('invalid', ['en']), 'en')
})
test('switches to the paired article and section under project and root bases', () => {
  const sections = {api: [{en:'section-2', zh:'section-2'}]}
  assert.equal(localeTarget('/Afterglow/en/api.html', '#section-2', 'zh', '/Afterglow/', sections), '/Afterglow/zh/api.html#section-2')
  assert.equal(localeTarget('/zh/api.html', '#missing', 'en', '/', sections), '/en/api.html')
  assert.equal(localeTarget('/Afterglow/en/', '', 'zh', '/Afterglow/', sections), '/Afterglow/zh/')
  assert.equal(localeTarget('/Afterglow/', '', 'en', '/Afterglow/', sections), '/Afterglow/en/')
})
