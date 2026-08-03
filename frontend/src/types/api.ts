// 与后端 schemas 对应的类型定义。
// 保持与 backend/xuwen/chat_api/schemas.py 一致；后端字段变动时记得同步。

export type AnalysisEventType =
  | 'milestone'
  | 'conflict'
  | 'reconciliation'
  | 'intimacy'
  | 'shared_activity'
  | 'emotional_shift'
  | 'separation'
  | 'daily'
  | 'other'

export interface AnalysisEvidence {
  quote: string
  session_id: string
  date: string
}

export interface TimelineEvent {
  event_id: string
  date: string
  title: string
  type: AnalysisEventType
  summary: string
  importance: number
  evidence: AnalysisEvidence[]
  session_ids: string[]
}

export interface TimelinePhase {
  title: string
  start_date: string
  end_date: string
  summary: string
  event_ids: string[]
}

export interface TimelineReport {
  generated_at: string
  source_message_count: number
  source_block_count: number
  events: TimelineEvent[]
  phases: TimelinePhase[]
}

export interface AnalysisObservation {
  subject: 'friend' | 'self' | 'both' | 'relationship' | 'unknown'
  dimension: string
  claim: string
  evidence: AnalysisEvidence[]
  confidence: number
  counterexamples: string[]
  alternative_explanations: string[]
}

export interface PersonalitySection {
  key: string
  title: string
  observations: AnalysisObservation[]
}

export interface PersonalityReport {
  generated_at: string
  disclaimer: string
  summary: string
  sections: PersonalitySection[]
}

export type ExperimentalCategory =
  | 'personality_hypothesis'
  | 'interpersonal_style'
  | 'attachment'
  | 'deception_pattern'
  | 'manipulation_intent'
  | 'mental_health_hypothesis'
  | 'manipulation_pattern'
  | 'internal_contradiction'
  | 'wellbeing_signal'

export interface ExperimentalSignal {
  subject: 'friend' | 'self' | 'both' | 'relationship' | 'unknown'
  category: ExperimentalCategory
  claim: string
  inference_basis: string
  conditions: string[]
  evidence: AnalysisEvidence[]
  confidence: number
  counterexamples: string[]
  alternative_explanations: string[]
}

export interface ExperimentalReport {
  generated_at: string
  disclaimer: string
  summary: string
  signals: ExperimentalSignal[]
}

export type ProactiveOpeningType =
  | 'greeting'
  | 'life_check'
  | 'care'
  | 'continue_topic'
  | 'self_share'
  | 'playful'
  | 'affection'
  | 'wake_ping'
  | 'short_ping'
  | 'question_probe'
  | 'night_ping'
  | 'other'

export type ProactiveReasonCategory =
  | 'continue_topic'
  | 'event_trigger'
  | 'care'
  | 'self_share'
  | 'question'
  | 'emotional_need'
  | 'routine'
  | 'greeting'
  | 'playful'
  | 'affection'
  | 'other'
  | 'unknown'

export interface ProactivePeriodCount {
  period: string
  count: number
}

export interface ProactiveOpeningRecord {
  opening_id: string
  session_id: string
  initiator: 'friend' | 'self'
  timestamp_ms: number
  occurred_at: string
  hour: number
  weekday: number
  idle_gap_minutes: number | null
  opening_type: ProactiveOpeningType
  messages: string[]
  content: string
  message_count: number
  previous_tail: string
  response_excerpt: string
  reason_category: ProactiveReasonCategory | null
  reason_summary: string
  time_explanation: string
  reason_evidence: AnalysisEvidence[]
  reason_confidence: number | null
  reason_alternative_explanations: string[]
}

export interface ProactiveAnalysisReport {
  schema_version: number
  generated_at: string
  session_gap_minutes: number
  source_session_count: number
  source_message_count: number
  eligible_session_count: number
  initiative_count: number
  opening_count: number
  friend_initiative_count: number
  self_started_count: number
  unknown_started_count: number
  initiative_rate: number
  range_start: string
  range_end: string
  span_days: number
  active_days: number
  average_per_30_days: number
  median_idle_gap_minutes: number | null
  hour_counts: number[]
  weekday_counts: number[]
  monthly_counts: ProactivePeriodCount[]
  opening_type_counts: Partial<Record<ProactiveOpeningType, number>>
  reason_counts: Partial<Record<ProactiveReasonCategory, number>>
  ai_analysis_status: 'not_requested' | 'completed' | 'partial' | 'failed'
  ai_analyzed_count: number
  openings: ProactiveOpeningRecord[]
}

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
  kind: 'friend' | 'window' | 'live' | 'response_pair' | 'history_image'
  text: string
  score: number
  rank: number
  timestamp_ms: number
  session_id?: string
  sender_name?: string
  source?: 'human_original' | 'human_original_image' | 'user_new' | 'ai_generated' | 'history' | 'live'
  warmth?: number
  image_sha?: string
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
  history_images?: MemorySource[]
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
  caller_id?: string
  client_message_id?: string
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
