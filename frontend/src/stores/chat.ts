import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { ChatMessage } from '@/types/api'

const STORAGE_KEY = 'xuwen.chat.v1'
const MAX_PERSISTED_MESSAGES = 100

function makeId(prefix = 'm'): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function loadPersisted(): { conversationId: string; messages: ChatMessage[] } | null {
  if (typeof localStorage === 'undefined') return null
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed.messages) || typeof parsed.conversationId !== 'string') return null
    return {
      conversationId: parsed.conversationId,
      messages: parsed.messages.map((m: ChatMessage) => ({ ...m, pending: false })),
    }
  } catch {
    return null
  }
}

export const useChatStore = defineStore('chat', () => {
  const persisted = loadPersisted()
  // 一次浏览器会话固定一个 conversation_id，用于后端 live 回写关联
  const conversationId = ref<string>(persisted?.conversationId || makeId('conv'))
  const messages = ref<ChatMessage[]>(persisted?.messages || [])
  // 当前流式输出中的 assistant 消息 id（仅一个）
  const streamingId = ref<string | null>(null)
  // 是否正在发送（用于禁用输入框）
  const isGenerating = ref(false)
  // 最近一次错误（用于在 UI 顶部提示）
  const lastError = ref<string | null>(null)

  function appendUserMessage(text: string, images: string[] = []): ChatMessage {
    const msg: ChatMessage = {
      id: makeId('u'),
      role: 'user',
      content: text,
      createdAt: Date.now(),
      ...(images.length > 0 ? { images } : {}),
    }
    messages.value.push(msg)
    return msg
  }

  function startAssistantMessage(): ChatMessage {
    const msg: ChatMessage = {
      id: makeId('a'),
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
      pending: true,
    }
    messages.value.push(msg)
    streamingId.value = msg.id
    return msg
  }

  function appendAssistantMessage(text: string): ChatMessage {
    const msg: ChatMessage = {
      id: makeId('a'),
      role: 'assistant',
      content: text,
      createdAt: Date.now(),
      pending: false,
    }
    messages.value.push(msg)
    return msg
  }

  function appendAssistantChunk(id: string, chunk: string) {
    const m = messages.value.find((x) => x.id === id)
    if (!m) return
    m.content += chunk
  }

  function finishAssistantMessage(id: string) {
    const m = messages.value.find((x) => x.id === id)
    if (m) m.pending = false
    if (streamingId.value === id) streamingId.value = null
  }

  function setMessageContent(id: string, text: string) {
    const m = messages.value.find((x) => x.id === id)
    if (m) m.content = text
  }

  function markMessageSilenced(id: string) {
    const m = messages.value.find((x) => x.id === id)
    if (!m) return
    // 沉默 = AI 选择不回复；正文清空，置 silenced 标记给气泡渲染层用
    m.content = ''
    m.silenced = true
    m.pending = false
    if (streamingId.value === id) streamingId.value = null
  }

  function attachMemorySources(id: string, sources: ChatMessage['memorySources']) {
    const m = messages.value.find((x) => x.id === id)
    if (m) m.memorySources = sources
  }

  function setMessageTraceId(id: string, traceId: string) {
    const m = messages.value.find((x) => x.id === id)
    if (m) m.traceId = traceId
  }

  function clear() {
    messages.value = []
    streamingId.value = null
    isGenerating.value = false
    conversationId.value = makeId('conv')
    lastError.value = null
  }

  function setError(err: string | null) {
    lastError.value = err
  }

  watch(
    [conversationId, messages],
    () => {
      if (typeof localStorage === 'undefined') return
      const data = {
        conversationId: conversationId.value,
        messages: messages.value.slice(-MAX_PERSISTED_MESSAGES).map((m) => ({
          ...m,
          pending: false,
        })),
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    },
    { deep: true },
  )

  return {
    conversationId,
    messages,
    streamingId,
    isGenerating,
    lastError,
    appendUserMessage,
    startAssistantMessage,
    appendAssistantMessage,
    appendAssistantChunk,
    finishAssistantMessage,
    setMessageContent,
    markMessageSilenced,
    attachMemorySources,
    setMessageTraceId,
    setError,
    clear,
  }
})
