import { createApp } from 'vue'
import { watch } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import './styles/tailwind.css'
import { useLocale } from './composables/useLocale'

const { locale } = useLocale()
if (typeof document !== 'undefined') {
  watch(locale, (value) => { document.documentElement.lang = value }, { immediate: true })
}

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
