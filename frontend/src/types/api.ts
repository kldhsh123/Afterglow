// 与后端 schemas 对应的类型定义。
// 保持与 backend/xuwen/chat_api/schemas.py 一致；后端字段变动时记得同步。

export type Role = 'system' | 'user' | 'assistant'

// OpenAI 多模态 content part
export interface TextPart {
  type: 'text'
  text: string
}
export interface ImagePart {
  type: 'image_url'
  image_url: { url: string; detail?: 'low' | 'high' | 'auto' }
}
export type ContentPart = TextPart | ImagePart

export interface UpdateInfo {
  check_enabled: boolean
  current_version: string
  latest_version: string | null
  is_outdated: boolean
  released_at: string | null
  release_url: string | null
  release_notes_preview: string | null
  last_checked_at_ms: number | null
  last_error: string | null
}

export interface AppInfo {
  app_name: string
  app_slogan: string
  friend_name: string
  self_name: string
  relationship_type: string
  relationship_description: string
  persona_template: string
  embedding_model: string
  chat_model: string
  version: string
  has_persona_card: boolean
  update: UpdateInfo | null
}

export interface ChatMessage {
  /** 本地生成的稳定 id，用于动效与 v-for key */
  id: string
  role: Role
  content: string
  /** 时间戳（ms） */
  createdAt: number
  /** 流式过程中显示的"正在打字"占位 */
  pending?: boolean
  /** 召回出处（前端用于"记忆溯源"浮窗） */
  memorySources?: MemorySource[]
  /** 用户消息附带的图片 data URLs（本地展示用） */
  images?: string[]
  /** 后端 request id，用于日志追踪 */
  traceId?: string
  /** AI 本轮选择沉默（不回复）；前端用灰色占位呈现 */
  silenced?: boolean
}

export interface MemorySource {
  chunk_id: string
  kind: 'friend' | 'window' | 'live' | 'response_pair'
  text: string
  score: number
  rank: number
  timestamp_ms: number
  session_id?: string
  sender_name?: string
  source?: 'human_original' | 'user_new' | 'ai_generated' | 'history' | 'live'
  warmth?: number
}

export interface MemoryStats {
  friend_messages: number
  dialogue_windows: number
  response_pairs?: number
  live_messages: number
  relationship_memories?: number
  writeback_enabled: boolean
  writeback_paused: boolean
}

export interface MemorySearchResponse {
  fused: MemorySource[]
  response_pairs?: MemorySource[]
  friend_examples: MemorySource[]
  dialogue_windows: MemorySource[]
  recent_live?: MemorySource[]
  trace_id?: string
}

/** OpenAI 兼容 chat/completions 请求体（前端发出去的） */
export interface ChatCompletionRequest {
  model?: string
  messages: { role: Role; content: string | ContentPart[] }[]
  stream: boolean
  temperature?: number
  top_p?: number
  max_tokens?: number
  conversation_id?: string
}

export interface PolicyHint {
  should_reply: boolean
  reply_mode: string
  user_state: string
  risk_level: string
  reason: string
  reply_delay_seconds: number
  reply_delay_reason: string
}

export interface ProactiveResponse {
  message: string
  life: Record<string, string | number>
  relationship_memory: string
  trace_id?: string
  policy?: PolicyHint | null
  silenced?: boolean
}

export interface ProactiveRequest {
  conversation_id: string
  reason?: string
  private_context?: string
  topic_hint?: string
}
