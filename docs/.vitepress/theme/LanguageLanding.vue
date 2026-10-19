<script setup>
import { onMounted } from 'vue'
import { withBase } from 'vitepress'
import { preferredLocale, storageKey } from './locale.mjs'

onMounted(() => {
  let stored
  try { stored = localStorage.getItem(storageKey) } catch {}
  location.replace(withBase(`/${preferredLocale(stored, navigator.languages || [navigator.language])}/`) + location.search + location.hash)
})
function remember(locale) {
  try { localStorage.setItem(storageKey, locale) } catch {}
}
</script>

<template>
  <main class="language-landing">
    <img :src="withBase('/brand/afterglow-icon.svg')" width="64" height="64" alt="Afterglow" class="landing-logo">
    <h1>Afterglow</h1>
    <p>Documentation · 使用文档</p>
    <p><a :href="withBase('/zh/')" lang="zh-CN" @click="remember('zh')">简体中文</a> · <a :href="withBase('/en/')" lang="en" @click="remember('en')">English</a></p>
  </main>
</template>

<style scoped>
.language-landing { max-width: 640px; margin: 80px auto; padding: 24px; text-align: center; }
.landing-logo { margin: 0 auto 20px; }
h1 { font-size: 36px; margin-bottom: 24px; }
p { margin: 16px 0; }
a { color: var(--vp-c-brand-1); text-decoration: underline; }
</style>
