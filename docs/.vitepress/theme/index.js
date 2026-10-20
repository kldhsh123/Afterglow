import DefaultTheme from 'vitepress/theme'
import { h, defineAsyncComponent } from 'vue'
import LanguageLanding from './LanguageLanding.vue'
import DocsHome from './DocsHome.vue'
import LanguageSelect from './LanguageSelect.vue'
import DocsMermaid from './DocsMermaid.vue'
import './style.css'

export default {
  extends: DefaultTheme,
  Layout: () => h(DefaultTheme.Layout, null, {
    'nav-bar-content-after': () => h(LanguageSelect),
  }),
  enhanceApp({ app }) {
    app.component('LanguageLanding', LanguageLanding)
    app.component('DocsHome', DocsHome)
    app.component('Mermaid', DocsMermaid)
    app.component('ConfigEditor', defineAsyncComponent(() => import('./ConfigEditor.vue')))
  },
}
