<script setup lang="ts">
// 消息列表 + 自动滚动；"到底"按钮统一放在 HomeView 工具栏，本组件不再渲染浮动按钮
import { computed, onMounted, ref, watch, nextTick } from 'vue'
import { useChatStore } from '@/stores/chat'
import { useAutoScroll } from '@/composables/useAutoScroll'
import MessageBubble from './MessageBubble.vue'
import TimeSeparator from './TimeSeparator.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import type { ChatMessage } from '@/types/api'

const chat = useChatStore()
const { attach, schedule, jumpToBottom } = useAutoScroll()
const containerRef = ref<HTMLElement | null>(null)

const emit = defineEmits<{ (e: 'pick-sample', text: string): void }>()

const hasMessages = computed(() => chat.messages.length > 0)

// 暴露给 HomeView 工具栏的"到底"按钮使用
defineExpose({ jumpToBottom })

// 判断是否要在消息之间插入时间分隔（间隔 > 30 分钟）
function shouldShowSeparator(prev: ChatMessage | undefined, curr: ChatMessage): boolean {
  if (!prev) return true
  return curr.createdAt - prev.createdAt > 30 * 60 * 1000
}

onMounted(() => {
  if (containerRef.value) attach(containerRef.value)
})

// 新增消息或内容追加都触发滚动
watch(
  () => [chat.messages.length, ...chat.messages.map((m) => m.content.length)],
  () => {
    nextTick(() => schedule())
  },
)
</script>

<template>
  <div class="relative h-full">
    <div
      ref="containerRef"
      class="h-full overflow-y-auto px-4 py-6 sm:px-6"
    >
      <div class="max-w-3xl mx-auto">
        <EmptyState v-if="!hasMessages" @pick="(t) => emit('pick-sample', t)" />
        <template v-else>
          <template v-for="(m, i) in chat.messages" :key="m.id">
            <TimeSeparator
              v-if="shouldShowSeparator(chat.messages[i - 1], m)"
              :timestamp="m.createdAt"
            />
            <MessageBubble :message="m" />
          </template>
          <!-- 留点底部空间避免被输入框遮挡 -->
          <div class="h-4" />
        </template>
      </div>
    </div>
  </div>
</template>
