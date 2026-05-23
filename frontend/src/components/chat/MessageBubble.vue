<script setup lang="ts">
// 单条消息气泡
// - 用户：右侧，背景 paper-shade（更深一档信笺色），稍硬朗
// - 朋友：左侧，无明显背景，衬线大字像写在纸上
// - 占位符消息（[图片]/[语音]/[撤回]）独立渲染为"模糊的胶片碎片"风格
// - 用户消息可附带图片（multimodal）

import { computed, ref } from 'vue'
import type { ChatMessage } from '@/types/api'
import { useSettingsStore } from '@/stores/settings'
import { useMemoryStore } from '@/stores/memory'
import { renderMarkdown } from '@/composables/markdown'
import MemoryAnchor from '@/components/memory/MemoryAnchor.vue'
import PlaceholderMessage from '@/components/common/PlaceholderMessage.vue'
import TypingIndicator from './TypingIndicator.vue'
import { X } from 'lucide-vue-next'

const props = defineProps<{
  message: ChatMessage
}>()

const settings = useSettingsStore()
const memory = useMemoryStore()

const isUser = computed(() => props.message.role === 'user')
const speaker = computed(() => {
  if (isUser.value) return settings.selfName || '我'
  return settings.friendName || '朋友'
})

const isPurePlaceholder = computed(() => {
  const t = props.message.content.trim()
  return /^\[(图片|语音|视频|文件|表情|动画表情|撤回|系统消息)\]$/.test(t)
})

const placeholderKind = computed(() => {
  const t = props.message.content.trim()
  const m = t.match(/^\[(图片|语音|视频|文件|表情|动画表情|撤回|系统消息)\]$/)
  return m?.[1] || ''
})

const html = computed(() => renderMarkdown(props.message.content))
const segmentHtmls = computed(() => {
  if (isUser.value) return [html.value]
  const parts = props.message.content
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean)
  if (parts.length === 0) return []
  return parts.map((part) => renderMarkdown(part))
})

const time = computed(() => {
  const d = new Date(props.message.createdAt)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
})

function openMemory() {
  if (props.message.memorySources && props.message.memorySources.length > 0) {
    memory.openMemoryModal(props.message.memorySources)
  }
}

const lightboxSrc = ref<string | null>(null)
function openLightbox(src: string) {
  lightboxSrc.value = src
}
function closeLightbox() {
  lightboxSrc.value = null
}
</script>

<template>
  <div
    class="flex w-full mb-4 animate-fade-up"
    :class="isUser ? 'justify-end' : 'justify-start'"
  >
    <!-- 朋友头像（左） -->
    <div
      v-if="!isUser"
      class="w-9 h-9 mr-3 mt-1 shrink-0 rounded-full bg-accent-soft/30 dark:bg-night-accent-soft/25
             flex items-center justify-center text-sm text-accent dark:text-night-accent
             font-serif"
      :title="speaker"
    >
      {{ speaker.slice(0, 1) }}
    </div>

    <div class="flex flex-col max-w-[80%] sm:max-w-[70%]" :class="isUser ? 'items-end' : 'items-start'">
      <span class="text-xs no-select text-ink-soft dark:text-night-text-soft mb-1 px-1">
        {{ speaker }}
        <span class="ml-2 opacity-60">{{ time }}</span>
      </span>

      <!-- 用户消息附带的图片 -->
      <div
        v-if="isUser && message.images && message.images.length > 0"
        class="flex flex-wrap gap-2 mb-2 justify-end"
      >
        <img
          v-for="(src, i) in message.images"
          :key="i"
          :src="src"
          class="max-w-[200px] max-h-[200px] rounded-lg shadow-letter cursor-zoom-in
                 hover:scale-[1.02] transition-transform"
          alt="发送的图片"
          @click="openLightbox(src)"
        />
      </div>

      <!-- 占位符消息（图片/语音/撤回...）走特殊渲染 -->
      <PlaceholderMessage
        v-if="isPurePlaceholder"
        :kind="placeholderKind"
        :is-user="isUser"
      />

      <!-- AI 选择沉默（不回复）：灰色低对比占位，不走 markdown -->
      <div
        v-else-if="!isUser && message.silenced"
        class="px-4 py-3 text-chat sm:text-chat-lg leading-relaxed italic
               text-ink-soft/70 dark:text-night-text-soft/70 select-none"
      >
        对方暂时没回复
      </div>

      <!-- 朋友正在打字 -->
      <TypingIndicator v-else-if="!isUser && message.pending && !message.content" />

      <!-- 正常文本 -->
      <div
        v-else-if="message.content"
        class="space-y-2"
        :class="isUser ? '' : 'w-full'"
      >
        <div
          v-for="(segmentHtml, idx) in segmentHtmls"
          :key="idx"
          class="px-4 py-3 text-chat sm:text-chat-lg leading-relaxed prose-letter"
          :class="
            isUser
              ? 'rounded-bubble rounded-tr-md bg-paper-shade dark:bg-night-bubble-user text-ink dark:text-night-text shadow-letter'
              : 'rounded-bubble rounded-tl-md text-ink dark:text-night-text'
          "
          v-html="segmentHtml"
        />
      </div>

      <!-- 记忆溯源 -->
      <MemoryAnchor
        v-if="!isUser && message.memorySources && message.memorySources.length > 0"
        class="mt-1"
        @click="openMemory"
      />
    </div>

    <!-- 用户头像（右） -->
    <div
      v-if="isUser"
      class="w-9 h-9 ml-3 mt-1 shrink-0 rounded-full bg-paper-shade dark:bg-night-bubble-user
             flex items-center justify-center text-sm text-ink dark:text-night-text font-serif"
      :title="speaker"
    >
      {{ speaker.slice(0, 1) }}
    </div>
  </div>

  <!-- 图片放大 lightbox -->
  <Teleport to="body">
    <div
      v-if="lightboxSrc"
      class="fixed inset-0 z-50 flex items-center justify-center
             bg-ink/70 dark:bg-black/80 backdrop-blur-sm p-4"
      @click.self="closeLightbox"
    >
      <button
        class="absolute top-4 right-4 p-2 rounded-full bg-paper-soft/90 dark:bg-night-bg-soft/90
               text-ink dark:text-night-text hover:scale-110 transition-transform"
        @click="closeLightbox"
        aria-label="关闭"
      >
        <X :size="20" />
      </button>
      <img
        :src="lightboxSrc"
        class="max-w-[90vw] max-h-[90vh] rounded-lg shadow-letter-strong"
        alt="图片放大"
      />
    </div>
  </Teleport>
</template>
