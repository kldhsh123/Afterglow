<script setup lang="ts">
// 版本更新提示横幅。从 /info 拉到 update.is_outdated=true 时显示。
// 用户可点"暂不更新"关掉（同版本 7 天内不再提示，localStorage 持久化）。
import { computed, ref } from 'vue'
import { ArrowUpCircle, ExternalLink, X } from 'lucide-vue-next'
import type { UpdateInfo } from '@/types/api'

const props = defineProps<{
  update: UpdateInfo | null
}>()

const STORAGE_KEY = 'xuwen.update.dismissed.v1'
const DISMISS_TTL_MS = 7 * 24 * 60 * 60 * 1000 // 7 天内不再提示同一版本

interface DismissedRecord {
  version: string
  ts: number
}

function loadDismissed(): DismissedRecord | null {
  if (typeof localStorage === 'undefined') return null
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const data = JSON.parse(raw) as DismissedRecord
    if (!data?.version || typeof data.ts !== 'number') return null
    if (Date.now() - data.ts > DISMISS_TTL_MS) return null
    return data
  } catch {
    return null
  }
}

const dismissed = ref<DismissedRecord | null>(loadDismissed())

const visible = computed(() => {
  const u = props.update
  if (!u || !u.is_outdated || !u.latest_version) return false
  // 7 天内对同一版本用户已经点过"暂不更新"
  if (dismissed.value && dismissed.value.version === u.latest_version) return false
  return true
})

function dismiss() {
  const v = props.update?.latest_version
  if (!v) return
  const record: DismissedRecord = { version: v, ts: Date.now() }
  dismissed.value = record
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(record))
  }
}
</script>

<template>
  <Transition
    enter-active-class="transition-all duration-300 ease-out"
    enter-from-class="opacity-0 -translate-y-2"
    leave-active-class="transition-all duration-200 ease-in"
    leave-to-class="opacity-0 -translate-y-2"
  >
    <div
      v-if="visible && update"
      class="mx-4 mt-3 p-3 rounded-xl bg-paper-soft/80 dark:bg-night-bg-soft/80
             text-ink dark:text-night-text text-sm
             flex items-start gap-3 border border-ink/10 dark:border-night-text/10
             shadow-sm"
    >
      <ArrowUpCircle :size="18" class="mt-0.5 shrink-0 text-accent" />
      <div class="flex-1 min-w-0">
        <p class="font-medium">
          Afterglow {{ update.latest_version }} 已发布
          <span class="ml-2 text-xs text-ink-soft dark:text-night-text-soft font-normal">
            当前 v{{ update.current_version }}
          </span>
        </p>
        <p
          v-if="update.release_notes_preview"
          class="mt-1 text-xs text-ink-soft dark:text-night-text-soft line-clamp-2"
        >
          {{ update.release_notes_preview }}
        </p>
        <div class="mt-2 flex flex-wrap gap-3 text-xs">
          <a
            v-if="update.release_url"
            :href="update.release_url"
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex items-center gap-1 text-accent hover:underline"
          >
            <span>查看变更</span>
            <ExternalLink :size="12" />
          </a>
          <button
            class="text-ink-soft dark:text-night-text-soft hover:text-ink dark:hover:text-night-text"
            @click="dismiss"
          >
            暂不更新
          </button>
        </div>
      </div>
      <button
        class="p-1 rounded-full hover:bg-ink/5 dark:hover:bg-night-text/10 shrink-0"
        aria-label="关闭"
        title="同版本 7 天内不再提示"
        @click="dismiss"
      >
        <X :size="14" />
      </button>
    </div>
  </Transition>
</template>
