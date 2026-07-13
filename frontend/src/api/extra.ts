import { jsonRequest, streamUrl, authHeaders } from '@/api/client'

export interface Sticker {
  name: string
  description: string
  owner: 'ai' | 'self' | 'shared'
  tags: string[]
  extension: string
  sha: string
  created_at_ms: number
  image_url: string
}

export function listStickers(owner?: 'ai' | 'self' | 'shared'): Promise<{ items: Sticker[] }> {
  const q = owner ? `?owner=${owner}` : ''
  return jsonRequest(`/v1/stickers${q}`)
}

export function createSticker(input: {
  name: string
  description: string
  data_url: string
  owner?: 'ai' | 'self' | 'shared'
  tags?: string[]
}): Promise<Sticker> {
  return jsonRequest('/v1/stickers', {
    method: 'POST',
    body: JSON.stringify({ owner: 'shared', tags: [], ...input }),
  })
}

export function updateSticker(
  name: string,
  patch: { description?: string; owner?: 'ai' | 'self' | 'shared'; tags?: string[] },
): Promise<Sticker> {
  return jsonRequest(`/v1/stickers/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function deleteSticker(name: string): Promise<{ status: string }> {
  return jsonRequest(`/v1/stickers/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export function stickerImageUrl(name: string): string {
  return streamUrl(`/v1/stickers/${encodeURIComponent(name)}/image`)
}

/* ---------- 文档 ---------- */

export interface DocumentExtractResult {
  filename: string
  extension: string
  text: string
  char_count: number
  estimated_tokens: number
}

export async function extractDocument(file: File): Promise<DocumentExtractResult> {
  const formData = new FormData()
  formData.append('file', file)
  const resp = await fetch(streamUrl('/v1/documents/extract'), {
    method: 'POST',
    body: formData,
    headers: authHeaders(),
  })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      detail = body?.detail || body?.error?.message || detail
    } catch {
      /* 忽略错误 */
    }
    throw new Error(detail)
  }
  return (await resp.json()) as DocumentExtractResult
}

export function getSupportedDocumentFormats(): Promise<{ extensions: string[] }> {
  return jsonRequest('/v1/documents/formats')
}

/* ---------- 调试 ---------- */

export function getDebugStats(): Promise<Record<string, unknown>> {
  return jsonRequest('/debug/stats')
}

export function getDebugConfig(): Promise<Record<string, unknown>> {
  return jsonRequest('/debug/config')
}

export function resetMetrics(): Promise<{ status: string }> {
  return jsonRequest('/debug/metrics/reset', { method: 'POST' })
}
