<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useChatStore } from '@/stores/chat'
import { cancelChatTurn } from '@/api/chat'
import { Settings, RotateCcw, Moon, Sun } from 'lucide-vue-next'

const settings = useSettingsStore()
const chat = useChatStore()
const router = useRouter()

const friendLabel = computed(() => settings.friendName || '朋友')
const title = computed(() => `与 ${friendLabel.value} 的对话`)

function toggleTheme() {
  if (settings.theme === 'dark') settings.theme = 'light'
  else if (settings.theme === 'light') settings.theme = 'dark'
  else settings.theme = settings.isDark ? 'light' : 'dark'
}

function newConversation() {
  if (!confirm('开启一段新对话？当前对话会被清空（向量库里的回写依旧保留）。')) return
  const oldConversationId = chat.conversationId
  window.dispatchEvent(new CustomEvent('afterglow:new-conversation'))
  chat.clear()
  void cancelChatTurn(oldConversationId).catch((e) => {
    console.warn('后端取消旧对话生成失败：', e)
  })
}

function goSettings() {
  router.push('/settings')
}
</script>

<template>
  <header
    class="sticky top-0 z-20 px-4 py-3 flex items-center justify-between
           backdrop-blur-md bg-paper/70 dark:bg-night-bg/60
           border-b border-ink/5 dark:border-night-text/10"
  >
    <div class="flex items-center gap-3 min-w-0">
      <div class="w-10 h-10 rounded-full bg-accent-soft/40 dark:bg-night-accent-soft/30
                  flex items-center justify-center font-serif text-lg
                  text-accent dark:text-night-accent shrink-0">
        {{ friendLabel.slice(0, 1) }}
      </div>
      <div class="min-w-0">
        <h1 class="text-base font-medium truncate">{{ title }}</h1>
        <p class="text-xs text-ink-soft dark:text-night-text-soft truncate">
          {{ settings.appSlogan }}
        </p>
      </div>
    </div>
    <div class="flex items-center gap-1">
      <button
        class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft
               dark:hover:text-night-text transition-colors"
        :title="settings.isDark ? '切换为明色' : '切换为暗色'"
        @click="toggleTheme"
      >
        <component :is="settings.isDark ? Sun : Moon" :size="18" />
      </button>
      <button
        class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft
               dark:hover:text-night-text transition-colors"
        title="开启新对话"
        @click="newConversation"
      >
        <RotateCcw :size="18" />
      </button>
      <button
        class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft
               dark:hover:text-night-text transition-colors"
        title="设置"
        @click="goSettings"
      >
        <Settings :size="18" />
      </button>
    </div>
  </header>
</template>
