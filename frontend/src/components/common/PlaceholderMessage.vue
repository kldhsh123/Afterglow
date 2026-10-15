<script setup lang="ts">
import { useLocale } from '@/composables/useLocale'
const { t } = useLocale()
// 占位符消息（图片 / 语音 / 撤回...）的诗化渲染
import { computed } from 'vue'
import { Image as ImageIcon, Mic, Film, FileText, EyeOff, MessageSquare } from 'lucide-vue-next'

const props = defineProps<{
  kind: string
  isUser: boolean
}>()

const meta = computed(() => {
  switch (props.kind) {
    case '图片':
      return {
        icon: ImageIcon,
        primary: t('remaining.aPhoto'),
        secondary: t('remaining.thisImageHasFadedOverTime'),
      }
    case '语音':
      return {
        icon: Mic,
        primary: t('remaining.aVoiceMessage'),
        secondary: t('remaining.theSoundIsNoLongerClear'),
      }
    case '视频':
      return {
        icon: Film,
        primary: t('remaining.aVideo'),
        secondary: t('remaining.theSceneHasFadedInMemory'),
      }
    case '文件':
      return {
        icon: FileText,
        primary: t('remaining.aFile'),
        secondary: t('remaining.noLongerAvailable'),
      }
    case '表情':
    case '动画表情':
      return { icon: ImageIcon, primary: t('polish.sticker'), secondary: t('remaining.noLongerAvailable') }
    case '撤回':
      return {
        icon: EyeOff,
        primary: t('remaining.aMessageWasRecalled'),
        secondary: t('remaining.wordsWrittenAndErased'),
      }
    case '系统消息':
      return {
        icon: MessageSquare,
        primary: t('remaining.systemMessage'),
        secondary: '',
      }
    default:
      return {
        icon: MessageSquare,
        primary: `[${props.kind}]`,
        secondary: '',
      }
  }
})
</script>

<template>
  <div
    class="px-4 py-3 rounded-bubble border border-dashed flex items-center gap-3
           text-sm italic select-none"
    :class="
      isUser
        ? 'border-ink/15 dark:border-night-text/15 bg-paper/40 dark:bg-night-bubble-user/40 text-ink-soft dark:text-night-text-soft rounded-tr-md'
        : 'border-ink/10 dark:border-night-text/10 text-ink-soft dark:text-night-text-soft rounded-tl-md'
    "
  >
    <component :is="meta.icon" :size="18" class="opacity-70 shrink-0" />
    <div class="leading-tight">
      <div>{{ meta.primary }}</div>
      <div v-if="meta.secondary" class="text-xs opacity-70 mt-0.5">{{ meta.secondary }}</div>
    </div>
  </div>
</template>
