import { authHeaders, jsonRequest, streamUrl } from '@/api/client'
import type {
  ChatCompletionRequest,
  PolicyHint,
  ProactiveRequest,
  ProactiveResponse,
} from '@/types/api'

/**
 * 与后端 /v1/chat/completions 建立 SSE 流式连接。
 *
 * - 使用 fetch + ReadableStream（不用 EventSource，因为它不支持 POST + Header）
 * - 通过 AbortController 支持中断
 * - 回调形式：onChunk 每收到一段文字调用；onDone / onError 结束
 */
export interface StreamHandlers {
  onChunk: (text: string) => void
  onTrace?: (traceId: string) => void
  onPolicy?: (policy: PolicyHint) => void
  /** AI 本轮选择沉默（finish_reason='silenced' 或 should_reply=false）。
   *  调用方应清空已打字内容、用灰色占位渲染气泡。 */
  onSilenced?: () => void
  onDone?: () => void
  onError?: (err: Error) => void
}

export interface StreamHandle {
  abort: () => void
  promise: Promise<void>
}

export function streamChat(
  req: ChatCompletionRequest,
  handlers: StreamHandlers,
): StreamHandle {
  const controller = new AbortController()
  const promise = (async () => {
    try {
      const resp = await fetch(streamUrl('/v1/chat/completions'), {
        method: 'POST',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          ...authHeaders(),
        },
        body: JSON.stringify({ ...req, stream: true }),
      })
      if (!resp.ok || !resp.body) {
        let detail = `HTTP ${resp.status}`
        try {
          const body = await resp.json()
          detail = body?.error?.message || body?.detail || detail
        } catch {
          /* ignore */
        }
        throw new Error(detail)
      }
      const traceId = resp.headers.get('x-request-id')
      if (traceId) handlers.onTrace?.(traceId)

      const reader = resp.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE 按 \n\n 分割每条事件
        let idx: number
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          parseEvent(rawEvent, handlers)
        }
      }
      // 残余 buffer
      if (buffer.trim()) parseEvent(buffer, handlers)
      handlers.onDone?.()
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e))
      if (err.name === 'AbortError') {
        handlers.onDone?.()
        return
      }
      handlers.onError?.(err)
    }
  })()
  return { abort: () => controller.abort(), promise }
}

export function requestProactiveTopic(
  conversationId: string,
  reason = 'manual',
  extra: Omit<Partial<ProactiveRequest>, 'conversation_id' | 'reason'> = {},
): Promise<ProactiveResponse> {
  return jsonRequest('/v1/companion/proactive', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: conversationId,
      reason,
      ...extra,
    }),
  })
}

/** 解析单条 SSE 事件（多行 `data: ...`），抽出 delta.content */
function parseEvent(raw: string, handlers: StreamHandlers): void {
  const lines = raw.split('\n')
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('data:')) continue
    const payload = trimmed.slice(5).trim()
    if (!payload || payload === '[DONE]') continue
    try {
      const parsed = JSON.parse(payload)
      if (parsed?.error?.message) {
        if (typeof parsed.error.trace_id === 'string') handlers.onTrace?.(parsed.error.trace_id)
        handlers.onError?.(new Error(parsed.error.message))
        return
      }
      if (typeof parsed?.trace_id === 'string') handlers.onTrace?.(parsed.trace_id)
      if (isPolicyHint(parsed?.policy)) {
        handlers.onPolicy?.(parsed.policy)
        // policy 阶段就能判沉默：should_reply=false 表示规则层已经决定不回。
        // 提前触发可避免后续 sentinel chunk 短暂上屏。
        if (parsed.policy.should_reply === false) handlers.onSilenced?.()
      }
      const choice = parsed?.choices?.[0]
      // finish_reason 由后端 _stream_silenced / AI 自主沉默时打到 'silenced'。
      // 注意：不依赖 sentinel 字符串本身（后端可配置），只看 finish_reason。
      if (choice?.finish_reason === 'silenced') {
        handlers.onSilenced?.()
        continue
      }
      const delta = choice?.delta
      const content = delta?.content
      if (typeof content === 'string' && content) {
        handlers.onChunk(content)
      }
    } catch {
      // 单行 JSON 解析失败时忽略，让其它行继续
      continue
    }
  }
}

function isPolicyHint(value: unknown): value is PolicyHint {
  if (!value || typeof value !== 'object') return false
  const obj = value as Record<string, unknown>
  return typeof obj.should_reply === 'boolean' && typeof obj.reply_mode === 'string'
}
