import { createApp } from 'vue'
import App from './App.vue'
import './styles.css'
import { useLocale } from './useLocale'
import { watch } from 'vue'

const { locale, t } = useLocale()
if (typeof document !== 'undefined') watch(locale, (value) => { document.documentElement.lang = value; document.title = t('title') }, { immediate: true })

const app = createApp(App)
app.mount('#app')
