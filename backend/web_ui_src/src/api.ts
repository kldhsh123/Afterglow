// 配置 UI 后端 API 客户端
// 路径前缀从当前 URL 推断（页面挂在 /config/ 就用 /config，挂在 /admin/ 就用 /admin），
// 这样后端改 CONFIG_UI_PATH_PREFIX 不用重新构建前端。
function detectConfigPrefix(): string {
  if (typeof window === 'undefined') return '/config'
  const p = window.location.pathname
  // 取第一段：/config/foo → /config；/foo/bar → /foo
  const m = p.match(/^(\/[^/]+)/)
  return m ? m[1] : '/config'
}

const CONFIG_PREFIX = detectConfigPrefix()
const TOKEN_KEY = 'afterglow.setup.token'

// token 合法字符：URL-safe base64 字符集（secrets.token_urlsafe 输出范围）
// 严格限制为 ASCII 可见字符，避免粘贴时带入的零宽空格 / 全角字符跑进 fetch header
// 触发 "String contains non ISO-8859-1 code point" 错误。
const TOKEN_CHAR_RE = /^[A-Za-z0-9\-_=.+/]+$/

function sanitizeToken(raw: string): string {
  // 1) 去掉所有空白（含全角空格 U+3000、零宽 U+200B/200C/200D/FEFF）
  // 2) trim 两侧
  return raw
    .replace(/[\s​‌‍﻿　]/g, '')
    .trim()
}

export function getToken(): string {
  if (typeof localStorage === 'undefined') return ''
  const stored = localStorage.getItem(TOKEN_KEY) || ''
  // 兼容老数据：取出来时清理一次
  const cleaned = sanitizeToken(stored)
  if (cleaned !== stored) {
    // 把清理后的值写回，避免下次再清理
    localStorage.setItem(TOKEN_KEY, cleaned)
  }
  return cleaned
}

export function setToken(token: string): void {
  if (typeof localStorage === 'undefined') return
  const cleaned = sanitizeToken(token)
  if (!cleaned) {
    localStorage.removeItem(TOKEN_KEY)
    return
  }
  if (!TOKEN_CHAR_RE.test(cleaned)) {
    // 抛错让 UI 显示明确提示，不要静默存进去再让 fetch 崩溃
    throw new Error(
      'Token 含有非法字符（仅允许英文/数字/-_=.+/）。请从后端控制台重新复制干净的字符串。',
    )
  }
  localStorage.setItem(TOKEN_KEY, cleaned)
}

function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

async function jsonRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(CONFIG_PREFIX + path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init.headers || {}),
    },
  })
  const text = await resp.text()
  let body: any = null
  if (text) {
    try { body = JSON.parse(text) } catch { body = { raw: text } }
  }
  if (!resp.ok) {
    const msg = body?.error?.message || body?.detail || `HTTP ${resp.status}`
    throw new Error(msg)
  }
  return body as T
}

// ---------- Types ----------

export interface SetupStatus {
  identity_ok: boolean
  chat_ok: boolean
  embedding_ok: boolean
  auth_ok: boolean
  wizard_completed: boolean
  env_path: string
  example_path: string
}

export interface Preset {
  id: string
  label: string
  base_url: string
  default_model: string
  apply_url: string
  hint: string
  // cross-rerank 用 {protocol: "jina"|"dashscope"}；其它分类目前为空对象
  extra?: Record<string, string>
}

export interface PresetsResponse {
  chat: Preset[]
  embedding: Preset[]
  label: Preset[]
  reranker: Preset[]
  cross_reranker: Preset[]
}

export interface TestResult {
  ok: boolean
  message: string
  detail?: string
  extra?: Record<string, any> | null
}

export interface UpdateValuesResult {
  ok: boolean
  restart_required?: boolean
  backup?: string | null
  rejected_keys?: string[]
  errors?: Array<{ field: string; message: string }>
}

export interface UploadedFile {
  name: string
  saved_as: string
  size: number
  format: string
  total_messages?: number
  candidates?: IdentityCandidate[]
  error?: string
}

export interface IdentityCandidate {
  name: string
  uid: string
  role_hint: 'self' | 'friend' | 'unknown'
}

export interface InspectResult {
  format: 'qqexporter_v5' | 'wechat_weflow' | 'unknown'
  total_messages: number
  candidates: IdentityCandidate[]
  error: string
}

export interface ImportTaskState {
  task_id: string
  files: string[]
  file_names: string[]
  status: 'pending' | 'parsing' | 'importing' | 'labeling' | 'persona' | 'done' | 'failed' | 'cancelled'
  progress: number
  stage: string
  detail: string
  error: string
  started_at: number
  finished_at: number | null
  report: any
}

// ---------- API ----------

export const api = {
  ping: () => jsonRequest<{ ok: boolean; ts: number }>('/ping'),
  status: () => jsonRequest<SetupStatus>('/status'),
  values: () => jsonRequest<{ values: Record<string, { set: boolean; value?: string; preview?: string }> }>('/values'),
  putValues: (values: Record<string, string>) =>
    jsonRequest<UpdateValuesResult>('/values', {
      method: 'PUT',
      body: JSON.stringify({ values, dry_run: false }),
    }),
  presets: () => jsonRequest<PresetsResponse>('/presets'),
  testChat: (base_url: string, api_key: string, model: string) =>
    jsonRequest<TestResult>('/test/chat', {
      method: 'POST',
      body: JSON.stringify({ base_url, api_key, model }),
    }),
  testEmbedding: (
    base_url: string,
    api_key: string,
    model: string,
    opts: { input_mode?: string; send_dimensions?: boolean; dim?: number } = {},
  ) =>
    jsonRequest<TestResult>('/test/embedding', {
      method: 'POST',
      body: JSON.stringify({ base_url, api_key, model, ...opts }),
    }),
  generateApiKey: () => jsonRequest<{ token: string }>('/generate/api-key', { method: 'POST' }),

  uploadFiles: async (files: File[]): Promise<{ uploaded: UploadedFile[] }> => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f, f.name)
    const resp = await fetch(CONFIG_PREFIX + '/import/upload', {
      method: 'POST',
      headers: { ...authHeaders() },
      body: fd,
    })
    const text = await resp.text()
    const body = text ? JSON.parse(text) : null
    if (!resp.ok) throw new Error(body?.error?.message || body?.detail || `HTTP ${resp.status}`)
    return body
  },

  inspectFile: async (file: File): Promise<InspectResult> => {
    const fd = new FormData()
    fd.append('file', file, file.name)
    const resp = await fetch(CONFIG_PREFIX + '/import/inspect', {
      method: 'POST',
      headers: { ...authHeaders() },
      body: fd,
    })
    const text = await resp.text()
    const body = text ? JSON.parse(text) : null
    if (!resp.ok) throw new Error(body?.error?.message || body?.detail || `HTTP ${resp.status}`)
    return body
  },

  startImport: (files: string[], file_names: string[], persona_source: string | null = null) =>
    jsonRequest<{ task_id: string; status: string }>('/import/start', {
      method: 'POST',
      body: JSON.stringify({ files, file_names, persona_source }),
    }),

  listTasks: () =>
    jsonRequest<{ active: ImportTaskState[]; all: ImportTaskState[] }>('/import'),

  getTask: (id: string) => jsonRequest<ImportTaskState>(`/import/${id}`),
  cancelTask: (id: string) =>
    jsonRequest<{ cancelled: boolean }>(`/import/${id}/cancel`, { method: 'POST' }),

  taskStreamUrl: (id: string): string => {
    const t = getToken()
    const tq = t ? `?token=${encodeURIComponent(t)}` : ''
    return CONFIG_PREFIX + `/import/${id}/stream${tq}`
  },
}
