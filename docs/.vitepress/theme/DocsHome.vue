<script setup>
import { computed } from 'vue'
import { useData, withBase } from 'vitepress'
import en from '../locales/en.mjs'
import zh from '../locales/zh-CN.mjs'

const { lang, isDark } = useData()
const locale = computed(() => lang.value.startsWith('zh') ? 'zh' : 'en')
const text = computed(() => (locale.value === 'zh' ? zh : en).home)
const logo = computed(() => withBase('/brand/afterglow-icon' + (isDark.value ? '' : '-light') + '.svg'))
function route(slug, hash = '') { return withBase('/' + locale.value + '/' + slug + '.html') + (hash ? '#' + hash : '') }
</script>

<template>
  <div class="docs-home">
    <section class="home-hero" aria-labelledby="home-title">
      <div class="hero-copy">
        <p class="home-eyebrow">{{ text.eyebrow }}</p>
        <h1 id="home-title">{{ text.title }}<span>{{ text.accent }}</span></h1>
        <p class="home-description">{{ text.description }}</p>
        <div class="home-actions">
          <a class="home-primary" :href="route('quickstart')">{{ text.primary }} <span aria-hidden="true">→</span></a>
          <a class="home-website" href="https://afterglow.kldhsh.top/">{{ text.secondary }} <span aria-hidden="true">↗</span></a>
        </div>
        <ul class="home-tags" aria-label="Afterglow">
          <li v-for="tag in text.tags" :key="tag">{{ tag }}</li>
        </ul>
      </div>
      <aside class="home-letter" :aria-label="text.letterLabel">
        <div class="letter-heading"><img :src="logo" width="30" height="30" alt=""><span>Afterglow</span></div>
        <p class="letter-label">{{ text.letterLabel }}</p>
        <p class="letter-title">{{ text.letterTitle }}<em>{{ text.letterAccent }}</em></p>
        <p class="letter-note">{{ text.letterNote }}</p>
        <ol class="letter-steps"><li v-for="step in text.letterSteps" :key="step">{{ step }}</li></ol>
        <img class="letter-emblem" :src="logo" width="150" height="150" alt="" aria-hidden="true">
      </aside>
    </section>
    <section class="home-start" aria-labelledby="start-title">
      <div class="start-heading"><h2 id="start-title">{{ text.startTitle }}</h2><p>{{ text.startDescription }}</p></div>
      <div class="start-paths">
        <a class="home-guide" :href="route('quickstart')">
          <span class="guide-number" aria-hidden="true">01</span>
          <h3>{{ text.guideTitle }}</h3><p>{{ text.guideDescription }}</p>
          <span class="guide-action">{{ text.guideAction }} <span aria-hidden="true">→</span></span>
        </a>
        <div class="home-routes">
          <a v-for="item in text.routes" :key="item.slug" :href="route(item.slug, item.hash)">
            <div><h3>{{ item.title }}</h3><p>{{ item.description }}</p></div><span aria-hidden="true">↗</span>
          </a>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.docs-home {
  --home-paper: #F5F1E4;
  --home-sheet: #FBF7E9;
  --home-ink: #1A2F4B;
  --home-muted: #4A5D7A;
  --home-accent: #8B5A2B;
  --home-rule: rgb(26 47 75 / 12%);
  --home-serif: 'Noto Serif SC', 'Songti SC', 'STSong', Georgia, serif;
  max-width: 1200px;
  margin: 0 auto;
  padding: 68px 32px 0;
  color: var(--home-ink);
}
:global(.dark .docs-home) {
  --home-paper: #152235;
  --home-sheet: #1C2D43;
  --home-ink: #F5F1E4;
  --home-muted: #B6C1D0;
  --home-accent: #D5AE7D;
  --home-rule: rgb(245 241 228 / 14%);
}
.home-hero { display: grid; grid-template-columns: 1.25fr 1fr; align-items: center; gap: 64px; padding: 12px 0 72px; }
.home-eyebrow { display: flex; align-items: center; gap: 12px; color: var(--home-accent); font-size: 12px; font-weight: 600; letter-spacing: .06em; }
.home-eyebrow::before { content: ''; width: 30px; height: 1px; background: currentColor; }
h1 { margin: 24px 0; font-family: var(--home-serif); font-size: clamp(36px, 4.2vw, 54px); font-weight: 600; line-height: 1.3; letter-spacing: -.045em; text-wrap: balance; }
h1 span { display: block; color: var(--home-accent); }
.home-description { max-width: 55ch; color: var(--home-muted); font-size: 16px; line-height: 1.95; text-wrap: pretty; }
.home-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 24px; margin-top: 30px; font-size: 14px; }
.home-primary { display: inline-flex; align-items: center; justify-content: space-between; gap: 34px; padding: 13px 23px; border: 1px solid var(--home-ink); border-radius: 999px; background: var(--home-ink); color: var(--home-paper); font-weight: 600; transition: transform 180ms, box-shadow 180ms; }
.home-primary:hover { transform: translateY(-2px); box-shadow: 0 7px 20px rgb(26 47 75 / 13%); }
.home-website { color: var(--home-muted); text-underline-offset: 6px; }
.home-website span { margin-left: 8px; color: var(--home-accent); }
.home-website:hover { text-decoration: underline; }
.home-tags { display: flex; flex-wrap: wrap; gap: 10px 20px; margin-top: 27px; color: var(--home-muted); font-size: 11px; }
.home-tags li { display: flex; align-items: center; gap: 7px; }
.home-tags li::before { content: ''; width: 4px; height: 4px; border-radius: 50%; background: var(--home-accent); opacity: .7; }
.home-letter { position: relative; isolation: isolate; padding: 30px 32px 26px; overflow: hidden; border: 1px solid var(--home-rule); border-radius: 5px 22px 22px 22px; background: var(--home-sheet); box-shadow: 0 3px 5px rgb(26 47 75 / 3%), 0 24px 60px rgb(26 47 75 / 8%); transform: rotate(2deg); }
.home-letter::before { position: absolute; inset: 0; z-index: -1; content: ''; background-image: repeating-linear-gradient(0deg, transparent 0 31px, var(--home-rule) 31px 32px); opacity: .18; }
.letter-heading { display: flex; align-items: center; gap: 10px; padding-bottom: 19px; border-bottom: 1px solid var(--home-rule); font-family: var(--home-serif); font-size: 17px; font-weight: 600; }
.letter-label { margin-top: 28px; color: var(--home-accent); font-size: 11px; }
.letter-title { position: relative; z-index: 1; margin-top: 16px; font-family: var(--home-serif); font-size: clamp(21px, 2.1vw, 27px); line-height: 1.8; font-weight: 500; text-wrap: balance; }
.letter-title em { display: block; font-style: normal; }
.letter-note { position: relative; z-index: 1; max-width: 30ch; margin-top: 21px; color: var(--home-muted); font-size: 12px; line-height: 1.9; }
.letter-steps { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 32px; padding-top: 17px; border-top: 1px solid var(--home-rule); font-size: 10px; color: var(--home-muted); }
.letter-steps li + li::before { content: '/'; padding-right: 12px; color: var(--home-accent); opacity: .5; }
.letter-emblem { position: absolute; right: -19px; bottom: 42px; z-index: -1; opacity: .055; transform: rotate(-15deg); }
.home-start { padding: 32px 0 55px; border-top: 1px solid var(--home-rule); }
.start-heading { display: grid; grid-template-columns: 1fr 1fr; align-items: start; gap: 40px; margin-bottom: 28px; }
h2 { max-width: 24ch; font-family: var(--home-serif); font-size: 26px; font-weight: 600; line-height: 1.5; letter-spacing: -.03em; text-wrap: balance; }
.start-heading > p { max-width: 51ch; color: var(--home-muted); font-size: 13px; line-height: 1.9; }
.start-paths { display: grid; grid-template-columns: 1fr 1.15fr; gap: 40px; }
.home-guide { display: flex; flex-direction: column; align-items: flex-start; padding: 24px 28px; border: 1px solid var(--home-rule); border-top: 2px solid var(--home-accent); border-radius: 3px 3px 14px 14px; background: var(--home-sheet); transition: transform 180ms, border-color 180ms; }
.home-guide:hover { transform: translateY(-3px); border-color: var(--home-accent); }
.guide-number { color: var(--home-accent); font-family: var(--home-serif); font-size: 15px; opacity: .7; }
h3 { margin-top: 14px; font-family: var(--home-serif); font-size: 18px; font-weight: 600; line-height: 1.5; }
.home-guide p { max-width: 40ch; margin: 10px 0 20px; color: var(--home-muted); font-size: 13px; line-height: 1.8; }
.guide-action { display: flex; align-items: center; gap: 18px; margin-top: auto; font-size: 12px; color: var(--home-accent); font-weight: 600; }
.home-routes a { display: flex; align-items: center; justify-content: space-between; gap: 20px; min-height: 88px; padding: 16px 0; border-bottom: 1px solid var(--home-rule); transition: padding 180ms; }
.home-routes a:first-child { padding-top: 5px; }
.home-routes a:hover { padding-left: 8px; }
.home-routes h3 { margin-top: 0; font-size: 15px; }
.home-routes p { margin-top: 4px; font-size: 12px; line-height: 1.7; color: var(--home-muted); }
.home-routes a > span { color: var(--home-accent); }
a:focus-visible { outline: 2px solid var(--home-accent); outline-offset: 5px; }
a:active { transform: translateY(1px); }
@media (max-width: 959px) {
  .docs-home { padding-top: 40px; }
  .home-hero { grid-template-columns: 1.1fr 1fr; gap: 30px; }
  .home-letter { padding: 22px; }
  h1 { font-size: 38px; }
  .start-paths { gap: 26px; }
}
@media (max-width: 639px) {
  .docs-home { padding: 34px 24px 0; }
  .home-hero { grid-template-columns: 1fr; gap: 40px; padding-bottom: 46px; }
  h1 { max-width: 18ch; font-size: 36px; line-height: 1.35; }
  .home-description { font-size: 14px; }
  .home-actions { gap: 20px; }
  .home-letter { max-width: 440px; margin: 0 7px; }
  .letter-title { font-size: 24px; }
  .letter-note { max-width: 32ch; }
  .start-heading, .start-paths { grid-template-columns: 1fr; gap: 16px; }
  .start-heading { margin-bottom: 24px; }
  h2 { font-size: 25px; }
  .home-guide { padding: 22px; }
  .home-routes a:first-child { padding-top: 16px; }
}
@media (prefers-reduced-motion: reduce) {
  a, .home-guide, .home-routes a { transition: none; }
}
</style>
