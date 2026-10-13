<script setup lang="ts">
// 设置面板
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useMemoryStore } from '@/stores/memory'
import {
  fetchInfo,
  fetchMemoryStats,
  pauseWriteback,
  resumeWriteback,
  triggerUpdateCheck,
} from '@/api/memory'
import type { AppInfo, UpdateInfo } from '@/types/api'
import {
  BookOpenText,
  Clock3,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  RefreshCw,
  Route,
  Sticker as StickerIcon,
} from 'lucide-vue-next'
import DiagnosticsPanel from '@/components/common/DiagnosticsPanel.vue'
import { useLocale } from '@/composables/useLocale'

const settings = useSettingsStore()
const { t, locale } = useLocale()
const memory = useMemoryStore()
const router = useRouter()
const info = ref<AppInfo | null>(null)
const updateInfo = ref<UpdateInfo | null>(null)
const checkingUpdate = ref(false)
const updateCheckHint = ref<string | null>(null)
const backendError = ref<string | null>(null)
const busy = ref(false)
const analysisGuideUrl = 'https://github.com/kldhsh123/Afterglow/wiki/高级与实验功能#聊天记录分析'

onMounted(async () => {
  try {
    info.value = await fetchInfo()
    updateInfo.value = info.value.update
  } catch (e) {
    backendError.value = t('settings.connectionError', { value: (e as Error).message })
  }
  try {
    memory.setStats(await fetchMemoryStats())
  } catch {
    /* 忽略错误 */
  }
})

async function checkForUpdate() {
  if (checkingUpdate.value) return
  checkingUpdate.value = true
  updateCheckHint.value = null
  try {
    const next = await triggerUpdateCheck()
    updateInfo.value = next
    if (next.last_error) {
      updateCheckHint.value = t('settings.checkFailed', { value: next.last_error })
    } else if (next.is_outdated && next.latest_version) {
      updateCheckHint.value = t('settings.newVersion', { value: next.latest_version })
    } else if (next.latest_version) {
      updateCheckHint.value = t('settings.latest')
    } else {
      updateCheckHint.value = t('settings.checkComplete')
    }
    // 3 秒后清掉一次性提示，留下面板里的静态字段
    setTimeout(() => {
      updateCheckHint.value = null
    }, 3000)
  } catch (e) {
    updateCheckHint.value = t('settings.checkError', { value: (e as Error).message })
  } finally {
    checkingUpdate.value = false
  }
}

function formatTimestamp(ms: number | null): string {
  if (!ms) return t('settings.notChecked')
  const d = new Date(ms)
  return d.toLocaleString(locale.value)
}

async function togglePause() {
  busy.value = true
  try {
    if (memory.stats?.writeback_paused) await resumeWriteback()
    else await pauseWriteback()
    memory.setStats(await fetchMemoryStats())
  } catch (e) {
    backendError.value = t('settings.operationError', { value: (e as Error).message })
  } finally {
    busy.value = false
  }
}

function back() {
  router.push('/')
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="max-w-2xl mx-auto px-4 py-6 sm:py-10 space-y-6">
      <header class="flex items-center gap-2">
        <button
          class="p-2 -ml-2 rounded-full text-ink-soft hover:text-ink
                 dark:text-night-text-soft dark:hover:text-night-text"
          @click="back"
        >
          <ChevronLeft :size="20" />
        </button>
        <h1 class="text-xl font-medium">{{ t('settings.title') }}</h1>
      </header>

      <!-- 后端连接 -->
      <section class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                      shadow-letter border border-ink/5 dark:border-night-text/10">
        <h2 class="text-base font-medium">{{ t('settings.backend') }}</h2>
        <label class="block">
          <span class="text-sm text-ink-soft dark:text-night-text-soft">{{ t('settings.backendUrl') }}</span>
          <input
            v-model="settings.backendBaseUrl"
            :placeholder="t('settings.backendUrlPlaceholder')"
            class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                   border border-ink/10 dark:border-night-text/10 outline-none
                   focus:ring-2 focus:ring-accent-soft"
          />
        </label>
        <label class="block">
          <span class="text-sm text-ink-soft dark:text-night-text-soft">
            {{ t('settings.apiKey') }}
          </span>
          <input
            v-model="settings.localApiKey"
            type="password"
            class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                   border border-ink/10 dark:border-night-text/10 outline-none
                   focus:ring-2 focus:ring-accent-soft"
          />
        </label>
        <p v-if="backendError" class="text-sm text-warning">{{ backendError }}</p>
        <div v-if="info" class="text-xs text-ink-soft dark:text-night-text-soft space-y-1">
          <div>{{ t('settings.backendVersion', { value: info.version }) }}</div>
          <div>{{ t('settings.chatModel', { value: info.chat_model }) }}</div>
          <div>{{ t('settings.embeddingModel', { value: info.embedding_model }) }}</div>
          <div>{{ t('settings.persona', { template: info.persona_template, relationship: info.relationship_type }) }}</div>
          <div>{{ info.has_persona_card ? t('settings.personaReady') : t('settings.personaMissing') }}</div>
        </div>
      </section>

      <!-- 版本与更新 -->
      <section class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                      shadow-letter border border-ink/5 dark:border-night-text/10">
        <div class="flex items-center justify-between">
          <h2 class="text-base font-medium">{{ t('settings.updates') }}</h2>
          <button
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs
                   text-ink-soft dark:text-night-text-soft
                   bg-paper/80 dark:bg-night-bg/80
                   border border-ink/10 dark:border-night-text/10
                   hover:text-ink dark:hover:text-night-text disabled:opacity-50"
            :disabled="checkingUpdate"
            @click="checkForUpdate"
          >
            <RefreshCw :size="12" :class="checkingUpdate ? 'animate-spin' : ''" />
            <span>{{ checkingUpdate ? t('settings.checking') : t('settings.checkNow') }}</span>
          </button>
        </div>

        <div class="text-xs text-ink-soft dark:text-night-text-soft space-y-1">
          <div>
            {{ t('settings.currentVersion') }}<span class="font-mono text-ink dark:text-night-text">v{{ updateInfo?.current_version ?? info?.version ?? '?' }}</span>
          </div>
          <div v-if="updateInfo?.latest_version">
            {{ t('settings.latestVersion') }}
            <span
              class="font-mono"
              :class="updateInfo.is_outdated
                ? 'text-accent dark:text-night-accent font-medium'
                : 'text-ink dark:text-night-text'"
            >v{{ updateInfo.latest_version }}</span>
            <span v-if="!updateInfo.is_outdated" class="ml-2 text-success">{{ t('settings.upToDate') }}</span>
            <span v-else class="ml-2 text-accent dark:text-night-accent">{{ t('settings.updateAvailable') }}</span>
          </div>
          <div v-if="updateInfo?.last_checked_at_ms">
            {{ t('settings.lastChecked', { value: formatTimestamp(updateInfo.last_checked_at_ms) }) }}
          </div>
          <div v-if="updateInfo?.check_enabled === false" class="text-warning">
            {{ t('settings.updateDisabled') }}
          </div>
          <div v-if="updateInfo?.last_error" class="text-warning">
            {{ t('settings.lastCheckFailed', { value: updateInfo.last_error }) }}
          </div>
        </div>

        <div
          v-if="updateInfo?.is_outdated && updateInfo.release_notes_preview"
          class="text-xs text-ink-soft dark:text-night-text-soft p-2 rounded-lg
                 bg-paper/60 dark:bg-night-bg/60 border border-ink/5 dark:border-night-text/10"
        >
          <div class="font-medium text-ink dark:text-night-text mb-1">{{ t('settings.releasePreview') }}</div>
          <p class="line-clamp-4">{{ updateInfo.release_notes_preview }}</p>
        </div>

        <div v-if="updateInfo?.release_url" class="flex gap-3 text-xs">
          <a
            :href="updateInfo.release_url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-accent dark:text-night-accent hover:underline"
          >{{ t('settings.viewReleaseNotes') }}</a>
        </div>

        <Transition
          enter-active-class="transition-opacity duration-200"
          enter-from-class="opacity-0"
          leave-active-class="transition-opacity duration-200"
          leave-to-class="opacity-0"
        >
          <p
            v-if="updateCheckHint"
            class="text-xs"
            :class="updateCheckHint.includes('失败') || updateCheckHint.includes('出错')
              || updateCheckHint.toLowerCase().includes('failed') || updateCheckHint.toLowerCase().includes('error')
              ? 'text-warning'
              : 'text-ink-soft dark:text-night-text-soft'"
          >
            {{ updateCheckHint }}
          </p>
        </Transition>
      </section>

      <!-- 身份与人格 -->
      <section class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                      shadow-letter border border-ink/5 dark:border-night-text/10">
        <h2 class="text-base font-medium">{{ t('settings.identity') }}</h2>
        <div class="grid grid-cols-2 gap-3">
          <label class="block">
            <span class="text-sm">{{ t('settings.yourName') }}</span>
            <input
              v-model="settings.selfName"
              class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10 outline-none focus:ring-2 focus:ring-accent-soft"
            />
          </label>
          <label class="block">
            <span class="text-sm">{{ t('settings.theirName') }}</span>
            <input
              v-model="settings.friendName"
              class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10 outline-none focus:ring-2 focus:ring-accent-soft"
            />
          </label>
        </div>
        <label class="block">
          <span class="text-sm">{{ t('settings.relationship') }}</span>
          <input
            v-model="settings.relationshipDescription"
            class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                   border border-ink/10 dark:border-night-text/10 outline-none focus:ring-2 focus:ring-accent-soft"
          />
        </label>
        <p class="text-xs text-ink-soft dark:text-night-text-soft">
          {{ t('settings.identityHint') }}
        </p>
      </section>

      <!-- 外观 -->
      <section class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                      shadow-letter border border-ink/5 dark:border-night-text/10">
        <h2 class="text-base font-medium">{{ t('settings.appearance') }}</h2>
        <div class="flex gap-2">
          <button
            v-for="themeMode in (['light','dark','system'] as const)"
            :key="themeMode"
            class="flex-1 py-2 rounded-lg border text-sm transition-colors"
            :class="
              settings.theme === themeMode
                ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                : 'border-ink/10 dark:border-night-text/10 text-ink-soft dark:text-night-text-soft hover:text-ink dark:hover:text-night-text'
            "
            @click="settings.theme = themeMode"
          >
            {{ themeMode === 'light' ? t('settings.light') : themeMode === 'dark' ? t('settings.dark') : t('settings.system') }}
          </button>
        </div>
        <label class="block">
          <span class="text-sm">{{ t('settings.fontScale', { value: Math.round(settings.fontScale * 100) }) }}</span>
          <input
            v-model.number="settings.fontScale"
            type="range"
            min="0.9"
            max="1.3"
            step="0.05"
            class="mt-1 w-full"
          />
        </label>
      </section>

      <!-- 记忆 -->
      <section v-if="memory.stats" class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                                          shadow-letter border border-ink/5 dark:border-night-text/10">
        <h2 class="text-base font-medium">{{ t('settings.memory') }}</h2>
        <div class="grid grid-cols-3 gap-3 text-center">
          <div>
            <div class="text-2xl font-medium">{{ memory.stats.friend_messages }}</div>
            <div class="text-xs text-ink-soft dark:text-night-text-soft">{{ t('settings.friendMessages') }}</div>
          </div>
          <div>
            <div class="text-2xl font-medium">{{ memory.stats.dialogue_windows }}</div>
            <div class="text-xs text-ink-soft dark:text-night-text-soft">{{ t('settings.dialogueWindows') }}</div>
          </div>
          <div>
            <div class="text-2xl font-medium">{{ memory.stats.live_messages }}</div>
            <div class="text-xs text-ink-soft dark:text-night-text-soft">{{ t('settings.liveMessages') }}</div>
          </div>
        </div>
        <button
          :disabled="busy"
          class="w-full py-2 rounded-lg border border-ink/10 dark:border-night-text/10 text-sm
                 hover:bg-paper dark:hover:bg-night-bg disabled:opacity-50"
          @click="togglePause"
        >
          {{ memory.stats.writeback_paused ? t('settings.resumeWriteback') : t('settings.pauseWriteback') }}
        </button>
      </section>
      <!-- 关系分析结果 -->
      <section
        class="rounded-lg bg-paper-soft dark:bg-night-bg-soft shadow-letter
               border border-ink/5 dark:border-night-text/10 divide-y divide-ink/10
               dark:divide-night-text/10"
      >
        <div class="p-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 class="font-medium">{{ t('settings.analysis') }}</h2>
            <p class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">
              {{ t('settings.analysisHint') }}
            </p>
          </div>
          <a
            :href="analysisGuideUrl"
            target="_blank"
            rel="noopener"
            class="inline-flex items-center gap-1.5 text-xs text-accent dark:text-night-accent hover:underline"
          >
            {{ t('settings.guide') }} <ExternalLink :size="13" />
          </a>
        </div>
        <button
          class="w-full flex items-center justify-between p-4 text-left"
          @click="router.push('/timeline')"
        >
          <span class="flex items-center gap-3">
            <Route :size="18" />
            <span>
              <span class="block font-medium">{{ t('settings.timeline') }}</span>
              <span class="block mt-0.5 text-xs text-ink-soft dark:text-night-text-soft">
                {{ t('settings.timelineHint') }}
              </span>
            </span>
          </span>
          <ChevronRight :size="17" class="text-ink-soft dark:text-night-text-soft" />
        </button>
        <button
          class="w-full flex items-center justify-between p-4 text-left"
          @click="router.push('/report')"
        >
          <span class="flex items-center gap-3">
            <BookOpenText :size="18" />
            <span>
              <span class="block font-medium">{{ t('settings.report') }}</span>
              <span class="block mt-0.5 text-xs text-ink-soft dark:text-night-text-soft">
                {{ t('settings.reportHint') }}
              </span>
            </span>
          </span>
          <ChevronRight :size="17" class="text-ink-soft dark:text-night-text-soft" />
        </button>
        <button
          class="w-full flex items-center justify-between p-4 text-left"
          @click="router.push('/proactive-analysis')"
        >
          <span class="flex items-center gap-3">
            <Clock3 :size="18" />
            <span>
              <span class="block font-medium">{{ t('settings.proactive') }}</span>
              <span class="block mt-0.5 text-xs text-ink-soft dark:text-night-text-soft">
                {{ t('settings.proactiveHint') }}
              </span>
            </span>
          </span>
          <ChevronRight :size="17" class="text-ink-soft dark:text-night-text-soft" />
        </button>
      </section>
      <!-- 表情包入口 -->
      <section
        class="rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft shadow-letter
               border border-ink/5 dark:border-night-text/10"
      >
        <button
          class="w-full flex items-center justify-between"
          @click="router.push('/stickers')"
        >
          <span class="flex items-center gap-2">
            <StickerIcon :size="18" />
            <span class="font-medium">{{ t('settings.stickers') }}</span>
          </span>
          <ChevronRight :size="17" class="text-ink-soft dark:text-night-text-soft" />
        </button>
      </section>

      <!-- 诊断 -->
      <DiagnosticsPanel />
    </div>
  </div>
</template>
