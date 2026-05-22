import { jsonRequest } from '@/api/client'
import type { AppInfo, MemorySearchResponse, MemoryStats, UpdateInfo } from '@/types/api'

export function fetchInfo(): Promise<AppInfo> {
  return jsonRequest<AppInfo>('/info')
}

/** 手动触发后端立即检查一次版本更新，返回最新 UpdateInfo。后端有 5 秒节流。 */
export function triggerUpdateCheck(): Promise<UpdateInfo> {
  return jsonRequest<UpdateInfo>('/info/check-update', { method: 'POST' })
}

export function fetchMemoryStats(): Promise<MemoryStats> {
  return jsonRequest<MemoryStats>('/memory/stats')
}

export function searchMemory(query: string, top_k = 12, conversation_id?: string): Promise<MemorySearchResponse> {
  return jsonRequest<MemorySearchResponse>('/memory/search', {
    method: 'POST',
    body: JSON.stringify({ query, top_k, conversation_id }),
  })
}

export function pauseWriteback(): Promise<{ status: string }> {
  return jsonRequest('/memory/writeback/pause', { method: 'POST' })
}

export function resumeWriteback(): Promise<{ status: string }> {
  return jsonRequest('/memory/writeback/resume', { method: 'POST' })
}

export function deleteMemory(table: string, id: string): Promise<{ status: string }> {
  const encoded = encodeURIComponent(id)
  return jsonRequest(`/memory/${table}/${encoded}`, { method: 'DELETE' })
}
