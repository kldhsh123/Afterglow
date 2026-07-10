import { useSettingsStore } from '@/stores/settings'

/** 把相对路径拼成完整 URL，并附带 API key（如有） */
function buildUrl(path: string): string {
  const settings = useSettingsStore()
  const base = settings.backendBaseUrl.replace(/\/$/, '')
  return base + path
}

export function authHeaders(): Record<string, string> {
  const settings = useSettingsStore()
  const headers: Record<string, string> = {}
  if (settings.localApiKey) {
    headers['Authorization'] = `Bearer ${settings.localApiKey}`
  }
  return headers
}

export async function jsonRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const url = buildUrl(path)
  const resp = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init.headers || {}),
    },
  })
  const contentType = resp.headers.get('content-type') || ''
  const traceId = resp.headers.get('x-request-id') || ''
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await readJson(resp, url)
      detail = body?.error?.message || body?.detail || detail
    } catch {
      /* ignore */
    }
    if (traceId) detail = `${detail} (trace_id: ${traceId})`
    throw new Error(detail)
  }
  if (!contentType.includes('application/json')) {
    const text = await resp.text()
    const prefix = text.trim().slice(0, 40)
    throw new Error(
      `后端返回的不是 JSON：${prefix || contentType}。请检查后端 API 地址或 Vite 代理配置。`,
    )
  }
  const body = await resp.json()
  if (
    traceId
    && body
    && typeof body === 'object'
    && !Array.isArray(body)
  ) {
    const bodyObj = body as Record<string, unknown>
    if (typeof bodyObj.trace_id !== 'string') {
      bodyObj.trace_id = traceId
    }
  }
  return body as T
}

export function streamUrl(path: string): string {
  return buildUrl(path)
}

async function readJson(resp: Response, url: string): Promise<Record<string, any>> {
  const contentType = resp.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    const text = await resp.text()
    const prefix = text.trim().slice(0, 40)
    throw new Error(
      `请求 ${url} 返回的不是 JSON：${prefix || contentType}`,
    )
  }
  return (await resp.json()) as Record<string, any>
}
