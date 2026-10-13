<script setup lang="ts">
// 诊断面板：嵌入到 SettingsView 的折叠区
import { onMounted, ref } from 'vue'
import { getDebugConfig, getDebugStats, resetMetrics } from '@/api/extra'
import { RefreshCw, RotateCcw } from 'lucide-vue-next'
import { useLocale } from '@/composables/useLocale'

const { t } = useLocale()

const stats = ref<Record<string, unknown> | null>(null)
const config = ref<Record<string, unknown> | null>(null)
const errorMsg = ref<string | null>(null)
const loading = ref(false)
const open = ref(false)

async function refresh() {
  loading.value = true
  errorMsg.value = null
  try {
    const [s, c] = await Promise.all([getDebugStats(), getDebugConfig()])
    stats.value = s
    config.value = c
  } catch (e) {
    errorMsg.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function clearMetrics() {
  if (!confirm(t('diagnostics.clearConfirm'))) return
  try {
    await resetMetrics()
    await refresh()
  } catch (e) {
    errorMsg.value = (e as Error).message
  }
}

function toggle() {
  open.value = !open.value
  if (open.value && stats.value === null) {
    void refresh()
  }
}

onMounted(() => {
  /* 不主动加载，等用户展开 */
})
</script>

<template>
  <section
    class="rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft shadow-letter
           border border-ink/5 dark:border-night-text/10"
  >
    <button
      class="w-full text-left flex items-center justify-between"
      @click="toggle"
    >
      <h2 class="text-base font-medium">{{ t('diagnostics.title') }}</h2>
      <span class="text-xs text-ink-soft dark:text-night-text-soft">
        {{ open ? t('diagnostics.collapse') : t('diagnostics.expand') }}
      </span>
    </button>

    <div v-if="open" class="mt-3 space-y-3">
      <div class="flex items-center gap-2">
        <button
          :disabled="loading"
          class="p-2 rounded-lg text-ink-soft hover:text-ink dark:text-night-text-soft
                 dark:hover:text-night-text disabled:opacity-50"
          :title="t('diagnostics.refresh')"
          @click="refresh"
        >
          <RefreshCw :size="16" :class="loading ? 'animate-spin' : ''" />
        </button>
        <button
          class="p-2 rounded-lg text-ink-soft hover:text-warning"
          :title="t('diagnostics.clear')"
          @click="clearMetrics"
        >
          <RotateCcw :size="16" />
        </button>
      </div>

      <p v-if="errorMsg" class="text-sm text-warning">{{ errorMsg }}</p>

      <div v-if="stats" class="space-y-3 text-sm">
        <div>
          <div class="font-medium mb-1">{{ t('diagnostics.vectorStore') }}</div>
          <div class="grid grid-cols-3 gap-2 text-center">
            <div class="rounded-lg bg-paper dark:bg-night-bg p-2">
              <div class="text-xl font-medium">{{ ((stats.memory as Record<string, number>)?.friend_messages ?? 0) }}</div>
              <div class="text-xs text-ink-soft dark:text-night-text-soft">{{ t('diagnostics.friendMessages') }}</div>
            </div>
            <div class="rounded-lg bg-paper dark:bg-night-bg p-2">
              <div class="text-xl font-medium">{{ ((stats.memory as Record<string, number>)?.dialogue_windows ?? 0) }}</div>
              <div class="text-xs text-ink-soft dark:text-night-text-soft">{{ t('diagnostics.dialogueWindows') }}</div>
            </div>
            <div class="rounded-lg bg-paper dark:bg-night-bg p-2">
              <div class="text-xl font-medium">{{ ((stats.memory as Record<string, number>)?.live_messages ?? 0) }}</div>
              <div class="text-xs text-ink-soft dark:text-night-text-soft">{{ t('diagnostics.newMessages') }}</div>
            </div>
          </div>
        </div>

        <div>
          <div class="font-medium mb-1">{{ t('diagnostics.writeback') }}</div>
          <pre class="text-xs bg-paper dark:bg-night-bg rounded-lg p-2 overflow-x-auto">{{ JSON.stringify(stats.writeback, null, 2) }}</pre>
        </div>

        <div>
          <div class="font-medium mb-1">{{ t('diagnostics.calls') }}</div>
          <pre class="text-xs bg-paper dark:bg-night-bg rounded-lg p-2 overflow-x-auto">{{ JSON.stringify(stats.calls, null, 2) }}</pre>
        </div>
      </div>

      <div v-if="config" class="space-y-2 text-sm">
        <div class="font-medium">{{ t('diagnostics.config') }}</div>
        <pre class="text-xs bg-paper dark:bg-night-bg rounded-lg p-2 overflow-x-auto max-h-[300px]">{{ JSON.stringify(config, null, 2) }}</pre>
      </div>
    </div>
  </section>
</template>
