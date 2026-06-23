<script setup lang="ts">
// 聊天主页面：组合 MessageList + ChatInput + 错误条 + 引导

import { computed, onMounted, ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import { useSettingsStore } from '@/stores/settings'
import { useMemoryStore } from '@/stores/memory'
import { requestProactiveTopic, streamChat, type StreamHandle } from '@/api/chat'
import { fetchInfo, fetchMemoryStats, searchMemory } from '@/api/memory'
import { useTypewriter } from '@/composables/useTypewriter'
import MessageList from '@/components/chat/MessageList.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import ErrorBanner from '@/components/common/ErrorBanner.vue'
import UpdateBanner from '@/components/common/UpdateBanner.vue'
import DebugDrawer from '@/components/common/DebugDrawer.vue'
import OnboardingDialog from '@/components/onboarding/OnboardingDialog.vue'
import { Bug, ChevronDown, Sparkles } from 'lucide-vue-next'
import type { ChatMessage, PolicyHint, UpdateInfo } from '@/types/api'

const chat = useChatStore()
const settings = useSettingsStore()
const memory = useMemoryStore()

let currentStream: StreamHandle | null = null
let activeTurnToken = 0
const pendingCallerMessageIds = new Set<string>()
const debugOpen = ref(false)
const updateInfo = ref<UpdateInfo | null>(null)
// MessageList 暴露的 jumpToBottom，由工具栏"到底"按钮调用
const messageListRef = ref<{ jumpToBottom: () => void } | null>(null)

const showOnboarding = computed(() => !settings.onboardingDone)

function jumpToBottom() {
  messageListRef.value?.jumpToBottom()
}

function replyDelayMs(policy?: PolicyHint | null): number {
  // 调试模式优先：本地覆盖后端给的延迟，便于压测 typewriter / 等待行为
  if (settings.debugForceReplyDelay) {
    const forced = Number(settings.debugReplyDelaySeconds ?? 0)
    if (!Number.isFinite(forced) || forced <= 0) return 0
    return Math.min(forced, 120) * 1000
  }
  const seconds = Number(policy?.reply_delay_seconds ?? 0)
  if (!Number.isFinite(seconds) || seconds <= 0) return 0
  return Math.min(seconds, 120) * 1000
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

onMounted(async () => {
  // 拉取后端 /info 同步元数据（不阻塞 UI）
  try {
    const info = await fetchInfo()
    settings.applyFromBackend({
      app_name: info.app_name,
      app_slogan: info.app_slogan,
      friend_name: info.friend_name,
      self_name: info.self_name,
      relationship_description: info.relationship_description,
    })
    updateInfo.value = info.update
  } catch (e) {
    // 不强制后端在线；只是同步元数据失败而已
    console.warn('无法获取后端 /info：', e)
  }
  try {
    const stats = await fetchMemoryStats()
    memory.setStats(stats)
  } catch {
    /* ignore */
  }
})

async function attachMemorySources(assistantId: string, query: string) {
  if (!query.trim()) return
  try {
    const result = await searchMemory(query, 6, chat.conversationId)
    const sources = result.fused.length > 0 ? result.fused : result.friend_examples
    if (sources.length > 0) chat.attachMemorySources(assistantId, sources)
  } catch {
    /* 静默失败 —— 没有溯源也不影响聊天 */
  }
}

function stopActiveGeneration() {
  activeTurnToken += 1
  if (currentStream) {
    currentStream.abort()
    currentStream = null
  }
  if (chat.streamingId) {
    chat.discardAssistantMessage(chat.streamingId)
  }
  chat.isGenerating = false
}

function toApiMessage(m: ChatMessage) {
  if (m.role === 'user' && m.images && m.images.length > 0) {
    const parts: { type: string; text?: string; image_url?: { url: string } }[] = []
    if (m.content) parts.push({ type: 'text', text: m.content })
    for (const url of m.images) parts.push({ type: 'image_url', image_url: { url } })
    return { role: m.role, content: parts as never }
  }
  return { role: m.role, content: m.content }
}

function send(text: string, images: string[] = []) {
  if (chat.isGenerating) stopActiveGeneration()
  chat.setError(null)

  const userMsg = chat.appendUserMessage(text, images)
  pendingCallerMessageIds.add(userMsg.id)
  const assistantMsg = chat.startAssistantMessage()
  chat.isGenerating = true
  const turnToken = ++activeTurnToken

  // 拟人化打字
  const typer = useTypewriter((piece) => {
    if (turnToken !== activeTurnToken) return
    chat.appendAssistantChunk(assistantMsg.id, piece)
  })
  let delayUntil = 0
  let silenced = false
  const runAfterReplyDelay = (fn: () => void) => {
    const guarded = () => {
      // 沉默场景下不再向 typer 推任何文本，避免 sentinel/已到达 chunk 上屏
      if (silenced) return
      if (turnToken === activeTurnToken && chat.streamingId === assistantMsg.id) fn()
    }
    const waitMs = Math.max(0, delayUntil - Date.now())
    if (waitMs > 0) {
      window.setTimeout(guarded, waitMs)
    } else {
      guarded()
    }
  }

  // 异步拉取记忆出处（不阻塞流式）
  void attachMemorySources(assistantMsg.id, text)

  // 构造给后端的 messages：含图片的 user 走 OpenAI 多模态格式
  const completedHistory = chat.messages
    .filter((m) => m.role === 'user' || m.role === 'assistant')
    .filter((m) => m.id !== assistantMsg.id)
    .filter((m) => !(m.role === 'user' && pendingCallerMessageIds.has(m.id)))
    .map(toApiMessage)
  const apiMessages = [...completedHistory, toApiMessage(userMsg)]

  currentStream = streamChat(
    {
      messages: apiMessages,
      stream: true,
      conversation_id: chat.conversationId,
      caller_id: chat.conversationId,
      client_message_id: userMsg.id,
    },
    {
      onTrace: (traceId) => {
        if (turnToken !== activeTurnToken) return
        chat.setMessageTraceId(assistantMsg.id, traceId)
      },
      onPolicy: (policy) => {
        if (turnToken !== activeTurnToken) return
        const until = Date.now() + replyDelayMs(policy)
        delayUntil = Math.max(delayUntil, until)
      },
      onSilenced: () => {
        if (turnToken !== activeTurnToken) return
        // AI 选择沉默：清空已显示文本、标记 silenced，气泡走灰色占位。
        if (silenced) return
        silenced = true
        chat.markMessageSilenced(assistantMsg.id)
        pendingCallerMessageIds.clear()
        chat.isGenerating = false
        currentStream = null
      },
      onChunk: (t) => runAfterReplyDelay(() => typer.pushText(t)),
      onDone: () => {
        if (turnToken !== activeTurnToken) return
        if (silenced) {
          // 沉默已在 onSilenced 里收尾，不再触发 typer.finish / setMessageContent
          chat.isGenerating = false
          currentStream = null
          return
        }
        runAfterReplyDelay(() => {
          typer.finish()
          // 等动画追上后再结束（最多再 1.5 秒）
          window.setTimeout(() => {
            if (turnToken !== activeTurnToken || chat.streamingId !== assistantMsg.id) return
            chat.finishAssistantMessage(assistantMsg.id)
            pendingCallerMessageIds.clear()
            chat.isGenerating = false
            currentStream = null
          }, 50)
        })
      },
      onError: (err) => {
        if (turnToken !== activeTurnToken) return
        chat.setError(`聊天出错：${err.message}`)
        // 把错误也显示成 assistant 的一句话
        if (!assistantMsg.content) {
          chat.setMessageContent(
            assistantMsg.id,
            `（连接出了点问题：${err.message}）`,
          )
        }
        chat.finishAssistantMessage(assistantMsg.id)
        chat.isGenerating = false
        currentStream = null
      },
    },
  )
}

function stop() {
  stopActiveGeneration()
}

function pickSample(text: string) {
  send(text, [])
}

async function startProactiveTopic() {
  if (chat.isGenerating) return
  chat.setError(null)
  const assistantMsg = chat.startAssistantMessage()
  chat.isGenerating = true
  try {
    const result = await requestProactiveTopic(chat.conversationId, 'manual')
    if (result.trace_id) chat.setMessageTraceId(assistantMsg.id, result.trace_id)
    const delay = replyDelayMs(result.policy)
    if (delay > 0) await wait(delay)
    // 后端在沉默场景会返回 silenced=true，且 message 是 sentinel；前端走灰色占位。
    if (result.silenced) {
      chat.markMessageSilenced(assistantMsg.id)
      return
    }
    chat.setMessageContent(assistantMsg.id, result.message.trim() || '嗯')
  } catch (e) {
    const err = e instanceof Error ? e : new Error(String(e))
    chat.setError(`主动话题出错：${err.message}`)
    chat.setMessageContent(
      assistantMsg.id,
      `（主动开话题失败：${err.message}）`,
    )
  } finally {
    chat.finishAssistantMessage(assistantMsg.id)
    chat.isGenerating = false
  }
}
</script>

<template>
  <div class="flex flex-col h-full">
    <UpdateBanner :update="updateInfo" />
    <ErrorBanner />
    <div class="flex-1 min-h-0">
      <MessageList ref="messageListRef" @pick-sample="pickSample" />
    </div>
    <div class="px-4 sm:px-6 pb-2">
      <div class="max-w-3xl mx-auto flex justify-start">
        <button
          class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full
                 text-xs text-ink-soft dark:text-night-text-soft
                 bg-paper-soft/80 dark:bg-night-bg-soft/80
                 border border-ink/5 dark:border-night-text/10
                 hover:text-ink dark:hover:text-night-text disabled:opacity-50"
          :disabled="chat.isGenerating"
          title="让对方主动开个话题"
          @click="startProactiveTopic"
        >
          <Sparkles :size="14" />
          <span>开个话题</span>
        </button>
        <button
          class="ml-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full
                 text-xs text-ink-soft dark:text-night-text-soft
                 bg-paper-soft/80 dark:bg-night-bg-soft/80
                 border border-ink/5 dark:border-night-text/10
                 hover:text-ink dark:hover:text-night-text"
          title="一键滑到最新消息"
          @click="jumpToBottom"
        >
          <ChevronDown :size="14" />
          <span>到底</span>
        </button>
        <button
          class="ml-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full
                 text-xs text-ink-soft dark:text-night-text-soft
                 bg-paper-soft/80 dark:bg-night-bg-soft/80
                 border border-ink/5 dark:border-night-text/10
                 hover:text-ink dark:hover:text-night-text"
          title="打开详细调试面板"
          @click="debugOpen = true"
        >
          <Bug :size="14" />
          <span>调试</span>
        </button>
      </div>
    </div>
    <ChatInput @send="send" @stop="stop" />
    <OnboardingDialog v-if="showOnboarding" />
    <DebugDrawer :open="debugOpen" @close="debugOpen = false" />
  </div>
</template>
