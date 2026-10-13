<script setup lang="ts">
import { useLocale } from '@/composables/useLocale'
const { t } = useLocale()
// "{{ t('common.loading') }}" 替代标准 typing 三点
</script>

<template>
  <div
    class="px-4 py-3 rounded-bubble rounded-tl-md text-ink-soft dark:text-night-text-soft
           italic text-sm flex items-center gap-2"
  >
    <span class="animate-pulse-soft inline-block w-1.5 h-1.5 rounded-full bg-accent dark:bg-night-accent" />
    <span class="animate-breathe">{{ t('common.loading') }}</span>
  </div>
</template>
