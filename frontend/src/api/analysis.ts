import { jsonRequest } from '@/api/client'
import type {
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
