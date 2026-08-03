import { jsonRequest } from '@/api/client'
import type {
  ExperimentalReport,
  PersonalityReport,
  ProactiveAnalysisReport,
  TimelineReport,
} from '@/types/api'

export function fetchTimeline(): Promise<TimelineReport> {
  return jsonRequest<TimelineReport>('/analysis/timeline')
}

export function fetchPersonalityReport(): Promise<PersonalityReport> {
  return jsonRequest<PersonalityReport>('/analysis/personality')
}

export function fetchProactiveAnalysis(): Promise<ProactiveAnalysisReport> {
  return jsonRequest<ProactiveAnalysisReport>('/analysis/proactive')
}

export function fetchExperimentalReport(): Promise<ExperimentalReport> {
  return jsonRequest<ExperimentalReport>('/analysis/experimental')
}
