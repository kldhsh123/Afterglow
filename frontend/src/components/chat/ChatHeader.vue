<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useChatStore } from '@/stores/chat'
import { cancelChatTurn } from '@/api/chat'
import { Settings, RotateCcw, Moon, Sun } from 'lucide-vue-next'
import { setLocale, useLocale } from '@/composables/useLocale'

const settings = useSettingsStore()
const { t, locale } = useLocale()
const chat = useChatStore()
const router = useRouter()

const friendLabel = computed(() => settings.friendName || '朋友')
const title = computed(() => t('chat.title', { name: friendLabel.value }))

function toggleTheme() {
  if (settings.theme === 'dark') settings.theme = 'light'
  else if (settings.theme === 'light') settings.theme = 'dark'
  else settings.theme = settings.isDark ? 'light' : 'dark'
}

function newConversation() {
  if (!confirm(t('chat.newConversationConfirm'))) return
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
      <label class="sr-only" for="language-switcher">{{ t('common.language') }}</label>
      <select
        id="language-switcher"
        v-model="locale"
        @change="setLocale(locale)"
        class="h-8 rounded-lg bg-paper/60 dark:bg-night-bg/60 border border-ink/10 dark:border-night-text/10 px-1.5 text-xs text-ink-soft dark:text-night-text-soft"
      >
        <option value="zh-CN">{{ t('common.chinese') }}</option>
        <option value="en">{{ t('common.english') }}</option>
      </select>
      <button
        class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft
               dark:hover:text-night-text transition-colors"
        :title="settings.isDark ? t('chat.switchToLight') : t('chat.switchToDark')"
        @click="toggleTheme"
      >
        <component :is="settings.isDark ? Sun : Moon" :size="18" />
      </button>
      <button
        class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft
               dark:hover:text-night-text transition-colors"
        :title="t('chat.newConversation')"
        @click="newConversation"
      >
        <RotateCcw :size="18" />
      </button>
      <button
        class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft
               dark:hover:text-night-text transition-colors"
        :title="t('common.settings')"
        @click="goSettings"
      >
        <Settings :size="18" />
      </button>
    </div>
  </header>
</template>
