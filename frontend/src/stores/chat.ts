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
  // 模拟连发：finishAssistantMessage 把 \n\n 拆出来的后续段落用 setTimeout 错峰推入
  // messages 数组。这里记录所有 pending timer，clear() 时取消，避免旧会话的延迟段
  // 落进新会话；同时回调里也校验 conversationId 没变才 push 作为兜底。
  const pendingSegmentTimers = new Set<ReturnType<typeof setTimeout>>()

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

    // 模拟社交平台连发：content 含双换行（\n\n）时拆成多条独立 message，
    // 每条都有独立的头像 / speaker / 时间戳。后端协议仍是单条响应（OpenAI 标准），
    // 拆条只在前端展示层做，不影响 chat completions 协议兼容性。
    // 后续段落错峰 2-5s 随机延迟出现，模拟真人在 IM 里"打完一条又补一条"的节奏。
    if (!m || !m.content) return
    const segments = m.content
      .split(/\n{2,}/)
      .map((s) => s.trim())
      .filter(Boolean)
    if (segments.length <= 1) return
    // 第一段立即留在原 message（保留 memorySources / traceId 等元信息）
    m.content = segments[0]
    // 后续段落用 setTimeout 错峰 push，累加延迟保持顺序。
    // 锁定当前 conversationId 快照：用户点"清空"换了新会话时，旧 timer 的回调
    // 看到 conversationId 不一致就丢弃，避免污染新会话；同时 timer 句柄被记录，
    // clear() 也会主动 cancel 所有 pending timer。
    const conversationAtSchedule = conversationId.value
    const insertedSegmentIds: string[] = []
    let cumulativeDelay = 0
    for (let i = 1; i < segments.length; i++) {
      const seg = segments[i]
      const segId = makeId('a')
      const stepDelay = 2000 + Math.random() * 3000 // 2-5 秒随机
      cumulativeDelay += stepDelay
      const handle = setTimeout(() => {
        pendingSegmentTimers.delete(handle)
        if (conversationId.value !== conversationAtSchedule) return
        const msg: ChatMessage = {
          id: segId,
          role: 'assistant',
          content: seg,
          createdAt: Date.now(),
          pending: false,
        }
        const anchors = [id, ...insertedSegmentIds]
        let insertAt = -1
        for (let j = anchors.length - 1; j >= 0; j--) {
          const idx = messages.value.findIndex((x) => x.id === anchors[j])
          if (idx !== -1) {
            insertAt = idx + 1
            break
          }
        }
        if (insertAt >= 0) messages.value.splice(insertAt, 0, msg)
        else messages.value.push(msg)
        insertedSegmentIds.push(segId)
      }, cumulativeDelay)
      pendingSegmentTimers.add(handle)
    }
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
    // 取消所有还没出现的错峰 segment timer，避免旧 segment 落进新会话
    pendingSegmentTimers.forEach(clearTimeout)
    pendingSegmentTimers.clear()
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
