import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'xuwen.settings.v1'

interface PersistedSettings {
  // 应用元数据（从后端 /info 拉取，但用户可在本地覆盖）
  appName: string
  appSlogan: string
  friendName: string
  selfName: string
  relationshipDescription: string
  // 主题
  theme: ThemeMode
  // 字号
  fontScale: number // 0.9 ~ 1.3
  // API 端点（默认走 Vite 代理 ""）
  backendBaseUrl: string
  /** 用户自填的 LLM/embedding 不在前端持久化（应通过后端 .env），但本地 dev API key 例外 */
  localApiKey: string
  /** 是否要求服务器开启 PII 脱敏（由 UI 反映，不会真改后端 settings） */
  preferPiiRedaction: boolean
  /** 是否首次启动完成引导 */
  onboardingDone: boolean
  /** 调试用：强制覆盖后端的 reply_delay_seconds（仅本地，不影响后端决策） */
  debugForceReplyDelay: boolean
  /** 调试用：本地覆盖的延迟秒数，启用 debugForceReplyDelay 时生效 */
  debugReplyDelaySeconds: number
}

const DEFAULTS: PersistedSettings = {
  appName: 'Afterglow',
  appSlogan: '把曾经对你好的话，续成往后的陪伴',
  friendName: '',
  selfName: '',
  relationshipDescription: '朋友',
  theme: 'system',
  fontScale: 1.0,
  backendBaseUrl: '',
  localApiKey: '',
  preferPiiRedaction: true,
  onboardingDone: false,
  debugForceReplyDelay: false,
  debugReplyDelaySeconds: 0,
}

function loadFromStorage(): PersistedSettings {
  if (typeof localStorage === 'undefined') return { ...DEFAULTS }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULTS }
    const parsed = JSON.parse(raw)
    return { ...DEFAULTS, ...parsed }
  } catch {
    return { ...DEFAULTS }
  }
}

function isSystemDark(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export const useSettingsStore = defineStore('settings', () => {
  const initial = loadFromStorage()

  const appName = ref(initial.appName)
  const appSlogan = ref(initial.appSlogan)
  const friendName = ref(initial.friendName)
  const selfName = ref(initial.selfName)
  const relationshipDescription = ref(initial.relationshipDescription)
  const theme = ref<ThemeMode>(initial.theme)
  const fontScale = ref(initial.fontScale)
  const backendBaseUrl = ref(initial.backendBaseUrl)
  const localApiKey = ref(initial.localApiKey)
  const preferPiiRedaction = ref(initial.preferPiiRedaction)
  const onboardingDone = ref(initial.onboardingDone)
  const debugForceReplyDelay = ref(initial.debugForceReplyDelay)
  const debugReplyDelaySeconds = ref(initial.debugReplyDelaySeconds)

  // 派生：当前实际是不是暗色
  const isDark = computed(() => {
    if (theme.value === 'dark') return true
    if (theme.value === 'light') return false
    return isSystemDark()
  })

  // 派生：浏览器标题
  const documentTitle = computed(() => {
    const target = friendName.value || '朋友'
    return `${appName.value} · 与${target}的对话`
  })

  // 把 isDark 同步到 <html class="dark">
  function applyDarkClass() {
    if (typeof document === 'undefined') return
    document.documentElement.classList.toggle('dark', isDark.value)
  }

  // 字号缩放
  function applyFontScale() {
    if (typeof document === 'undefined') return
    document.documentElement.style.fontSize = `${Math.round(fontScale.value * 100)}%`
  }

  watch(isDark, applyDarkClass, { immediate: true })
  watch(fontScale, applyFontScale, { immediate: true })
  watch(documentTitle, (t) => {
    if (typeof document !== 'undefined') document.title = t
  }, { immediate: true })

  // 监听系统主题
  if (typeof window !== 'undefined' && window.matchMedia) {
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    mql.addEventListener?.('change', () => {
      if (theme.value === 'system') applyDarkClass()
    })
  }

  // 持久化
  function persist() {
    if (typeof localStorage === 'undefined') return
    const data: PersistedSettings = {
      appName: appName.value,
      appSlogan: appSlogan.value,
      friendName: friendName.value,
      selfName: selfName.value,
      relationshipDescription: relationshipDescription.value,
      theme: theme.value,
      fontScale: fontScale.value,
      backendBaseUrl: backendBaseUrl.value,
      localApiKey: localApiKey.value,
      preferPiiRedaction: preferPiiRedaction.value,
      onboardingDone: onboardingDone.value,
      debugForceReplyDelay: debugForceReplyDelay.value,
      debugReplyDelaySeconds: debugReplyDelaySeconds.value,
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  }

  // 任何变化都持久化（rough but effective）
  watch(
    [
      appName,
      appSlogan,
      friendName,
      selfName,
      relationshipDescription,
      theme,
      fontScale,
      backendBaseUrl,
      localApiKey,
      preferPiiRedaction,
      onboardingDone,
      debugForceReplyDelay,
      debugReplyDelaySeconds,
    ],
    persist,
    { deep: false },
  )

  function applyFromBackend(info: {
    app_name: string
    app_slogan: string
    friend_name: string
    self_name: string
    relationship_description: string
  }) {
    // 后端值作为默认；用户已修改过的不覆盖
    if (!appName.value || appName.value === DEFAULTS.appName) appName.value = info.app_name || DEFAULTS.appName
    if (!appSlogan.value || appSlogan.value === DEFAULTS.appSlogan) appSlogan.value = info.app_slogan || DEFAULTS.appSlogan
    if (!friendName.value) friendName.value = info.friend_name
    if (!selfName.value) selfName.value = info.self_name
    if (!relationshipDescription.value || relationshipDescription.value === DEFAULTS.relationshipDescription) {
      relationshipDescription.value = info.relationship_description || DEFAULTS.relationshipDescription
    }
  }

  function reset() {
    Object.assign(
      {
        appName,
        appSlogan,
        friendName,
        selfName,
        relationshipDescription,
        theme,
        fontScale,
        backendBaseUrl,
        localApiKey,
        preferPiiRedaction,
        onboardingDone,
      },
      DEFAULTS,
    )
    appName.value = DEFAULTS.appName
    appSlogan.value = DEFAULTS.appSlogan
    friendName.value = DEFAULTS.friendName
    selfName.value = DEFAULTS.selfName
    relationshipDescription.value = DEFAULTS.relationshipDescription
    theme.value = DEFAULTS.theme
    fontScale.value = DEFAULTS.fontScale
    backendBaseUrl.value = DEFAULTS.backendBaseUrl
    localApiKey.value = DEFAULTS.localApiKey
    preferPiiRedaction.value = DEFAULTS.preferPiiRedaction
    onboardingDone.value = DEFAULTS.onboardingDone
    debugForceReplyDelay.value = DEFAULTS.debugForceReplyDelay
    debugReplyDelaySeconds.value = DEFAULTS.debugReplyDelaySeconds
  }

  return {
    appName,
    appSlogan,
    friendName,
    selfName,
    relationshipDescription,
    theme,
    fontScale,
    backendBaseUrl,
    localApiKey,
    preferPiiRedaction,
    onboardingDone,
    debugForceReplyDelay,
    debugReplyDelaySeconds,
    isDark,
    documentTitle,
    applyFromBackend,
    reset,
  }
})
