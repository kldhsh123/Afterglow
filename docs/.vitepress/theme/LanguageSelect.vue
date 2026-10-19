<script setup>
import { computed } from 'vue'
import { useData, useRouter } from 'vitepress'
import { localeTarget, storageKey } from './locale.mjs'
import sections from '../../.site/sections.json'
import en from '../locales/en.mjs'
import zh from '../locales/zh-CN.mjs'
const texts = {en, zh}

const { lang, site } = useData()
const router = useRouter()
const locale = computed(() => lang.value.startsWith('zh') ? 'zh' : 'en')
function change(event) {
  const next = event.target.value
  try { localStorage.setItem(storageKey, next) } catch {}
  const headings = [...document.querySelectorAll('.vp-doc h2[id], .vp-doc h3[id]')]
  const current = headings.filter(heading => heading.getBoundingClientRect().top <= 180).at(-1)
  const hash = current ? `#${current.id}` : location.hash
  const target = localeTarget(location.pathname, hash, next, site.value.base, sections)
  const url = new URL(target, location.origin)
  url.search = location.search
  router.go(url.pathname + url.search + url.hash)
}
</script>

<template>
  <label class="docs-language">
    <span class="sr-only">{{ texts[locale].languageSelect }}</span>
    <select :value="locale" @change="change">
      <option value="zh">{{ zh.label }}</option>
      <option value="en">{{ en.label }}</option>
    </select>
  </label>
</template>

<style scoped>
.docs-language { display: inline-flex; align-items: center; margin: 0 12px; }
select { color: var(--vp-c-text-1); background: var(--vp-c-bg); border: 1px solid var(--vp-c-divider); border-radius: 6px; padding: 5px 8px; font: inherit; cursor: pointer; }
select:focus-visible { outline: 2px solid var(--vp-c-brand-1); outline-offset: 2px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
