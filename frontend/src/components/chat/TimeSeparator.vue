<script setup lang="ts">
import { useLocale } from '@/composables/useLocale'
const { locale } = useLocale()
// 用细线 + 居中时间表示对话分隔（强化"时间感"）
import { computed } from 'vue'

const props = defineProps<{ timestamp: number }>()

const text = computed(() => {
  const d = new Date(props.timestamp)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return d.toLocaleTimeString(locale.value, { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleString(locale.value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
})
</script>

<template>
  <div class="flex items-center gap-3 my-4 text-xs text-ink-soft dark:text-night-text-soft no-select">
    <div class="flex-1 h-px bg-ink/10 dark:bg-night-text/10" />
    <span>{{ text }}</span>
    <div class="flex-1 h-px bg-ink/10 dark:bg-night-text/10" />
  </div>
</template>
