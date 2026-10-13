import { createApp } from 'vue'
import App from './App.vue'
import './styles.css'
import { useLocale } from './useLocale'
import { watch } from 'vue'

const { locale } = useLocale()
if (typeof document !== 'undefined') watch(locale, (value) => { document.documentElement.lang = value }, { immediate: true })

const app = createApp(App)
app.mount('#app')
