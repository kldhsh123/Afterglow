<script setup lang="ts">
// 记忆溯源浮窗：展示召回的历史片段。
// 优先级：fused（最终融合） / friend_examples / dialogue_windows / history_images
// UI：居中半透明卡片，按 ESC 或点遮罩关闭，含 GSAP 进出动效。

import { computed, onMounted, watch, ref } from 'vue'
import gsap from 'gsap'
import { X } from 'lucide-vue-next'
import { useMemoryStore } from '@/stores/memory'

const memory = useMemoryStore()
const containerRef = ref<HTMLDivElement | null>(null)
const cardRef = ref<HTMLDivElement | null>(null)

const isOpen = computed(() => memory.openSources !== null)
const sources = computed(() => memory.openSources || [])

function close() {
  memory.closeMemoryModal()
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
})

watch(isOpen, async (val) => {
  if (val) {
    // 等 DOM
    await Promise.resolve()
    if (containerRef.value) {
      gsap.fromTo(
        containerRef.value,
        { opacity: 0 },
        { opacity: 1, duration: 0.25, ease: 'power2.out' },
      )
    }
    if (cardRef.value) {
      gsap.fromTo(
        cardRef.value,
        { y: 16, opacity: 0, filter: 'blur(8px)' },
        { y: 0, opacity: 1, filter: 'blur(0px)', duration: 0.4, ease: 'power3.out' },
      )
    }
  }
})

function fmtTime(ts: number): string {
  if (!ts) return '时间未知'
  return new Date(ts).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function sourceLabel(kind: string): string {
  if (kind === 'window') return '当时的多轮对话'
  if (kind === 'friend') return '当时的一句话'
  if (kind === 'response_pair') return '当时的问答'
  if (kind === 'history_image') return '历史图片描述'
  return '最近的对话'
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="isOpen"
      ref="containerRef"
      class="fixed inset-0 z-50 flex items-center justify-center
             bg-ink/40 dark:bg-black/60 backdrop-blur-sm p-4"
      @click.self="close"
    >
      <div
        ref="cardRef"
        class="relative w-full max-w-lg max-h-[80vh] overflow-hidden
               bg-paper-soft dark:bg-night-bg-soft rounded-2xl shadow-letter-strong
               flex flex-col"
        role="dialog"
        aria-modal="true"
      >
        <header class="px-5 py-4 flex items-center justify-between border-b border-ink/5 dark:border-night-text/10">
          <div>
            <h2 class="text-base font-medium">来自这段记忆</h2>
            <p class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5">
              这条回应的灵感来自以下历史片段
            </p>
          </div>
          <button
            class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft
                   dark:hover:text-night-text"
            @click="close"
            aria-label="关闭"
          >
            <X :size="18" />
          </button>
        </header>
        <div class="overflow-y-auto px-5 py-4 space-y-4">
          <div
            v-for="(s, i) in sources"
            :key="s.chunk_id"
            class="rounded-xl p-3 bg-paper/60 dark:bg-night-bg/60
                   border border-ink/5 dark:border-night-text/10"
          >
            <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft mb-2">
              <span class="px-1.5 py-0.5 rounded-full bg-accent-soft/30 dark:bg-night-accent-soft/30 text-accent dark:text-night-accent">
                {{ i + 1 }}
              </span>
              <span>{{ fmtTime(s.timestamp_ms) }}</span>
              <span class="opacity-70">· {{ sourceLabel(s.kind) }}</span>
            </div>
            <div
              v-if="s.kind === 'history_image' && s.image_sha"
              class="mb-2 text-xs text-ink-soft dark:text-night-text-soft break-all"
            >
              image_sha {{ s.image_sha }}
            </div>
            <p class="text-sm whitespace-pre-line leading-relaxed">{{ s.text }}</p>
          </div>
          <div v-if="sources.length === 0" class="text-center text-ink-soft dark:text-night-text-soft text-sm py-8">
            这条回应是从空白里生出来的。
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
