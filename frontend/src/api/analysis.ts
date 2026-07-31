import { jsonRequest } from '@/api/client'
import type {
  AnalysisTask,
  ExperimentalReport,
  PersonalityReport,
  TimelineReport,
} from '@/types/api'

export function fetchTimeline(): Promise<TimelineReport> {
  return jsonRequest<TimelineReport>('/analysis/timeline')
}

export function fetchPersonalityReport(): Promise<PersonalityReport> {
  return jsonRequest<PersonalityReport>('/analysis/personality')
}

export function fetchExperimentalReport(): Promise<ExperimentalReport> {
  return jsonRequest<ExperimentalReport>('/analysis/experimental')
}

export function startAnalysis(): Promise<AnalysisTask> {
  return jsonRequest<AnalysisTask>('/analysis/start', {
    method: 'POST',
    body: JSON.stringify({ timeline: true, personality: true, resume: true }),
  })
}

export function fetchAnalysisTask(taskId: string): Promise<AnalysisTask> {
  return jsonRequest<AnalysisTask>(`/analysis/${encodeURIComponent(taskId)}`)
}

export function cancelAnalysis(taskId: string): Promise<{ status: string }> {
  return jsonRequest<{ status: string }>(`/analysis/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
  })
}
