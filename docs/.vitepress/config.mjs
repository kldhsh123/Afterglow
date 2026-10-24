import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'
import pages from '../pages.json' with { type: 'json' }

import en from './locales/en.mjs'
import zh from './locales/zh-CN.mjs'

const locales = { en, zh }
const base = process.env.DOCS_BASE || '/Afterglow/'
const qqGroupUrl = 'https://qm.qq.com/cgi-bin/qm/qr?k=7rvmrvR100Is9aAp0ZsjmfiG7e0Cv6ZB&jump_from=webapi&authKey=mEN/epjvPHcT+Sb61/XO0Mi5egs2xJBhZm9Wm5MmgEWrpNa0ZOY3fzUf+pvqfijD'
function theme(locale) {
  const text = locales[locale]
  return {
    nav: [
      {text: text.website, link: 'https://afterglow.kldhsh.top/'},
      ...['quickstart', 'desktop', 'configuration', 'api'].map(slug => ({text: text.pages[slug], link: `/${locale}/${slug}`})),
      {text: text.qqGroup, link: qqGroupUrl},
    ],
    sidebar: Object.entries(text.groups).map(([group, title]) => ({
      text: title, collapsed: false,
      items: pages.filter(page => page.group === group).map(page => ({
        text: text.pages[page.slug], link: `/${locale}/${page.slug === 'index' ? '' : page.slug}`,
      })),
    })),
    outline: {level: [2, 3], label: text.outline},
    docFooter: text.docFooter,
    sidebarMenuLabel: text.sidebarMenuLabel,
    skipToContentLabel: text.skipToContent,
    langMenuLabel: text.languageSelect,
    returnToTopLabel: text.returnToTopLabel,
    darkModeSwitchLabel: text.darkModeSwitchLabel,
    lightModeSwitchTitle: text.lightModeSwitchTitle,
    darkModeSwitchTitle: text.darkModeSwitchTitle,
    notFound: text.notFound,
    editLink: {
      pattern: ({frontmatter}) => `https://github.com/kldhsh123/Afterglow/edit/main/docs/${frontmatter.sourcePath}`,
      text: text.edit,
    },
    footer: {message: 'AGPL-3.0-or-later', copyright: 'Afterglow'},
  }
}
export default withMermaid(defineConfig({
  title: 'Afterglow',
  description: en.description,
  base,
  head: [
    ['link', {rel: 'preconnect', href: 'https://fonts.googleapis.com'}],
    ['link', {rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: ''}],
    ['link', {rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;500;600;700&display=swap'}],
    ['link', {rel:'icon', type:'image/svg+xml', href:`${base}brand/favicon.svg`}],
    ['link', {rel:'alternate icon', href:`${base}brand/favicon.ico`}],
  ],
  srcDir: '.site',
  cleanUrls: false,
  locales: {
    root: { label: en.label, lang: en.lang, themeConfig: { nav: [], sidebar: [], notFound: en.notFound } },
    ...Object.fromEntries(Object.entries(locales).map(([locale, text]) => [locale, {
      label: text.label, lang: text.lang, title: text.title, description: text.description, themeConfig: theme(locale),
    }])),
  },
  themeConfig: {
    // Our selector maps articles/sections; keep hidden built-in links on valid locale roots.
    i18nRouting: false,
    siteTitle: 'Afterglow',
    logo: {light:'/brand/afterglow-icon-light.svg', dark:'/brand/afterglow-icon.svg', alt:'Afterglow'},
    socialLinks: [
      { icon: 'github', link: 'https://github.com/kldhsh123/Afterglow' },
      { icon: 'discord', link: 'https://discord.gg/CKrvgCyK4y' },
    ],
    search: {provider: 'local', options: {miniSearch: {options: {tokenize: (text) => [...new Intl.Segmenter('zh', {granularity: 'word'}).segment(text)].filter(part => part.isWordLike).map(part => part.segment)}}, locales: Object.fromEntries(
      Object.entries(locales).map(([locale, text]) => [locale, {translations: text.search}]),
    )}},
  },
  markdown: { lineNumbers: true },
}))
