<script setup lang="ts">
// 首次启动空状态：欢迎语 + 几个示例引导
import { computed } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { Heart } from 'lucide-vue-next'
import { useLocale } from '@/composables/useLocale'

const settings = useSettingsStore()
const { t, list } = useLocale()
const target = computed(() => settings.friendName || t('remaining.friend'))
const samples = computed(() => list('empty.samples'))

const emit = defineEmits<{ (e: 'pick', text: string): void }>()
</script>

<template>
  <div class="flex flex-col items-center justify-center h-full py-16 px-6 text-center">
    <div class="w-16 h-16 rounded-full bg-accent-soft/30 dark:bg-night-accent-soft/30
                flex items-center justify-center text-accent dark:text-night-accent mb-5">
      <Heart :size="28" />
    </div>
    <h2 class="text-xl font-medium mb-2">{{ settings.appSlogan === '把曾经对你好的话，续成往后的陪伴' ? t('onboarding.slogan') : settings.appSlogan }}</h2>
    <p class="text-sm text-ink-soft dark:text-night-text-soft max-w-md mb-8">{{ t('empty.talk', { name: target }) }}</p>
    <div class="flex flex-wrap gap-2 justify-center max-w-md">
      <button
        v-for="s in samples"
        :key="s"
        class="px-4 py-2 rounded-full text-sm bg-paper-soft dark:bg-night-bg-soft
               border border-ink/5 dark:border-night-text/10
               text-ink-soft dark:text-night-text-soft
               hover:text-ink dark:hover:text-night-text
               hover:bg-paper-shade dark:hover:bg-night-bubble-user transition-colors"
        @click="emit('pick', s)"
      >
        {{ s }}
      </button>
    </div>
  </div>
</template>
