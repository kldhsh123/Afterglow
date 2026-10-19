<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  BarChart3,
  CalendarClock,
  ChevronLeft,
  ChevronDown,
  ChevronUp,
  Clock3,
  MessageCircle,
  RefreshCw,
  Users,
} from 'lucide-vue-next'
import { fetchProactiveAnalysis } from '@/api/analysis'
import type {
  ProactiveAnalysisReport,
  ProactiveOpeningRecord,
  ProactiveOpeningType,
  ProactiveReasonCategory,
} from '@/types/api'
import { useLocale } from '@/composables/useLocale'

const router = useRouter()
const { t, locale } = useLocale()
const report = ref<ProactiveAnalysisReport | null>(null)
const loading = ref(true)
const error = ref('')
const visibleLimit = ref(80)
const expanded = ref(new Set<string>())

const weekdayLabels = computed(() => [t('remaining.mon'), t('remaining.tue'), t('remaining.wed'), t('remaining.thu'), t('remaining.fri'), t('remaining.sat'), t('remaining.sun')])
const openingTypeLabels = computed<Record<ProactiveOpeningType, string>>(() => ({
  greeting: t('proactive.openingTypes.greeting'), life_check: t('proactive.openingTypes.life_check'), care: t('proactive.openingTypes.care'), continue_topic: t('proactive.openingTypes.continue_topic'), self_share: t('proactive.openingTypes.self_share'), playful: t('proactive.openingTypes.playful'), affection: t('proactive.openingTypes.affection'), wake_ping: t('proactive.openingTypes.wake_ping'), short_ping: t('proactive.openingTypes.short_ping'), question_probe: t('proactive.openingTypes.question_probe'), night_ping: t('proactive.openingTypes.night_ping'), other: t('proactive.openingTypes.other'),
}))
const reasonLabels = computed<Record<ProactiveReasonCategory, string>>(() => ({
  continue_topic: t('proactive.reasons.continue_topic'), event_trigger: t('proactive.reasons.event_trigger'), care: t('proactive.reasons.care'), self_share: t('proactive.reasons.self_share'), question: t('proactive.reasons.question'), emotional_need: t('proactive.reasons.emotional_need'), routine: t('proactive.reasons.routine'), greeting: t('proactive.reasons.greeting'), playful: t('proactive.reasons.playful'), affection: t('proactive.reasons.affection'), other: t('proactive.reasons.other'), unknown: t('proactive.reasons.unknown'),
}))

const hourMax = computed(() => Math.max(1, ...(report.value?.hour_counts ?? [1])))
const weekdayMax = computed(() => Math.max(1, ...(report.value?.weekday_counts ?? [1])))
const monthMax = computed(() => Math.max(1, ...(report.value?.monthly_counts.map((item) => item.count) ?? [1])))
const topHour = computed(() => {
  const counts = report.value?.hour_counts ?? []
  const index = counts.indexOf(Math.max(...counts))
  return index >= 0 && counts[index] > 0 ? `${index}:00` : t('proactive.unknown')
})
const topWeekday = computed(() => {
  const counts = report.value?.weekday_counts ?? []
  const index = counts.indexOf(Math.max(...counts))
  return index >= 0 && counts[index] > 0 ? weekdayLabels.value[index] : t('proactive.unknown')
})
const visibleOpenings = computed(() => report.value?.openings.slice(0, visibleLimit.value) ?? [])
const hasMore = computed(() => Boolean(report.value && visibleLimit.value < report.value.openings.length))

async function load() {
  loading.value = true
  error.value = ''
  try {
    report.value = await fetchProactiveAnalysis()
  } catch (reason) {
    error.value = `${t('errors.loadFailed')}: ${(reason as Error).message}`
  } finally {
    loading.value = false
  }
}

function formatOccurred(record: ProactiveOpeningRecord) {
  const date = new Date(record.occurred_at)
  if (Number.isNaN(date.getTime())) return record.occurred_at
  return new Intl.DateTimeFormat(locale.value, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

function toggle(openingId: string) {
  const next = new Set(expanded.value)
  next.has(openingId) ? next.delete(openingId) : next.add(openingId)
  expanded.value = next
}

function formatGap(minutes: number | null) {
  if (minutes === null) return t('proactive.silenceStart')
  if (minutes < 60) return t('proactive.silenceMinutes', { value: minutes })
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? t('proactive.silenceHoursMinutes', { hours, minutes: rest }) : t('proactive.silenceHours', { value: hours })
}

onMounted(load)
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="max-w-5xl mx-auto px-4 py-6 sm:px-8 sm:py-10">
      <header class="flex items-center justify-between gap-4 border-b border-ink/15 dark:border-night-text/15 pb-5">
        <div class="flex items-center gap-2 min-w-0">
          <button
            class="p-2 -ml-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft dark:hover:text-night-text"
            :aria-label="t('proactive.back')"
            @click="router.push('/settings')"
          >
            <ChevronLeft :size="20" />
          </button>
          <div>
            <h1 class="text-xl font-medium">{{ t('proactive.title') }}</h1>
            <p v-if="report" class="mt-1 font-sans text-xs tabular-nums text-ink-soft dark:text-night-text-soft">
              {{ t('proactive.dateRange', { start: report.range_start, end: report.range_end, count: report.source_message_count.toLocaleString(locale) }) }}
            </p>
          </div>
        </div>
        <button
          class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft dark:hover:text-night-text"
          :title="t('proactive.refresh')"
          :aria-label="t('proactive.refreshLabel')"
          @click="load"
        >
          <RefreshCw :size="18" :class="{ 'animate-spin': loading }" />
        </button>
      </header>

      <div v-if="loading" class="min-h-72 flex items-center justify-center text-ink-soft dark:text-night-text-soft">
        <RefreshCw :size="20" class="animate-spin mr-2" /> {{ t('proactive.loading') }}
      </div>
      <div v-else-if="error" class="min-h-72 flex flex-col items-center justify-center text-center">
        <MessageCircle :size="30" class="mb-3 text-ink-soft dark:text-night-text-soft" />
        <p class="font-medium">{{ t('proactive.unavailable') }}</p>
        <p class="text-sm mt-2 max-w-md text-ink-soft dark:text-night-text-soft">{{ error }}</p>
        <p class="mt-3 text-xs text-ink-soft dark:text-night-text-soft">
          {{ t('proactive.commandHint') }}
        </p>
      </div>
      <template v-else-if="report">
        <aside class="my-8 border-l-2 border-accent-soft pl-4 text-sm leading-6 text-ink-soft dark:text-night-text-soft">
          {{ t('proactive.explanation') }}
        </aside>

        <div
          v-if="report.ai_analysis_status !== 'completed'"
          class="mb-8 border-y border-warning/30 py-3 text-sm text-warning"
        >
          {{ t('proactive.aiStatus') }}{{ report.ai_analysis_status === 'partial' ? t('proactive.aiPartial', { done: report.ai_analyzed_count, total: report.opening_count }) : report.ai_analysis_status === 'failed' ? t('proactive.aiFailed') : t('proactive.aiDisabled') }}
        </div>

        <section class="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-5 py-7 border-y border-ink/15 dark:border-night-text/15">
          <div>
            <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft"><MessageCircle :size="15" />{{ t('proactive.initiative') }}</div>
            <div class="mt-2 text-3xl font-medium tabular-nums">{{ report.initiative_count.toLocaleString(locale) }}</div>
            <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">{{ t('proactive.times') }}</div>
          </div>
          <div>
            <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft"><BarChart3 :size="15" />{{ t('proactive.rate') }}</div>
            <div class="mt-2 text-3xl font-medium tabular-nums">{{ Math.round(report.initiative_rate * 100) }}%</div>
            <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">{{ t('proactive.rateHint') }}</div>
          </div>
          <div>
            <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft"><CalendarClock :size="15" />{{ t('proactive.frequency') }}</div>
            <div class="mt-2 text-3xl font-medium tabular-nums">{{ report.average_per_30_days }}</div>
            <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">{{ t('proactive.per30') }}</div>
          </div>
          <div>
            <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft"><Clock3 :size="15" />{{ t('proactive.typical') }}</div>
            <div class="mt-2 text-3xl font-medium tabular-nums">{{ topHour }}</div>
            <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">{{ topWeekday }} {{ t('proactive.mostCommon') }}</div>
          </div>
        </section>

        <section class="pt-10">
          <div class="flex items-end justify-between gap-4">
            <div>
              <h2 class="text-lg font-medium">{{ t('proactive.hourly') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">{{ t('proactive.hourlyHint') }}</p>
            </div>
            <span class="font-sans text-xs tabular-nums text-ink-soft dark:text-night-text-soft">{{ report.initiative_count }} {{ t('proactive.times') }}</span>
          </div>
          <div class="mt-6 grid grid-cols-12 sm:grid-cols-24 gap-1.5 items-end h-36 border-b border-ink/15 dark:border-night-text/15 pb-6">
            <div
              v-for="(count, hour) in report.hour_counts"
              :key="hour"
              class="group relative h-full flex flex-col justify-end items-center min-w-0"
            >
              <div
                class="w-full max-w-5 rounded-t-sm bg-accent-soft/80 dark:bg-night-accent-soft/80 transition-all group-hover:bg-accent dark:group-hover:bg-night-accent"
                :style="{ height: `${count ? Math.max(8, count / hourMax * 100) : 2}%` }"
                :title="t('proactive.hourCount', { hour, count })"
              />
              <span v-if="hour % 3 === 0" class="absolute top-full mt-2 font-sans text-[10px] tabular-nums text-ink-soft dark:text-night-text-soft">{{ hour }}</span>
            </div>
          </div>
        </section>

        <section class="grid lg:grid-cols-[1fr_1fr] gap-10 pt-12">
          <div>
            <h2 class="text-lg font-medium">{{ t('proactive.weekdays') }}</h2>
            <div class="mt-5 space-y-3">
              <div v-for="(count, index) in report.weekday_counts" :key="index" class="grid grid-cols-[3rem_1fr_2.5rem] items-center gap-3 text-sm">
                <span>{{ weekdayLabels[index] }}</span>
                <div class="h-2 bg-ink/8 dark:bg-night-text/10 rounded-sm overflow-hidden">
                  <div class="h-full bg-accent dark:bg-night-accent" :style="{ width: `${count / weekdayMax * 100}%` }" />
                </div>
                <span class="font-sans text-right tabular-nums text-xs text-ink-soft dark:text-night-text-soft">{{ count }}</span>
              </div>
            </div>
          </div>
          <div>
            <h2 class="text-lg font-medium">{{ t('proactive.monthly') }}</h2>
            <div v-if="report.monthly_counts.length" class="mt-5 space-y-3">
              <div v-for="item in report.monthly_counts" :key="item.period" class="grid grid-cols-[5rem_1fr_2.5rem] items-center gap-3 text-sm">
                <span class="font-sans tabular-nums">{{ item.period }}</span>
                <div class="h-2 bg-ink/8 dark:bg-night-text/10 rounded-sm overflow-hidden">
                  <div class="h-full bg-accent-soft dark:bg-night-accent-soft" :style="{ width: `${item.count / monthMax * 100}%` }" />
                </div>
                <span class="font-sans text-right tabular-nums text-xs text-ink-soft dark:text-night-text-soft">{{ item.count }}</span>
              </div>
            </div>
            <p v-else class="mt-5 text-sm text-ink-soft dark:text-night-text-soft">{{ t('proactive.noRecords') }}</p>
          </div>
        </section>

        <section class="pt-12">
          <div class="flex items-end justify-between gap-4 border-b border-ink/15 dark:border-night-text/15 pb-4">
            <div>
              <h2 class="text-lg font-medium">{{ t('proactive.openings') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">{{ t('proactive.openingsHint', { count: report.opening_count }) }}</p>
            </div>
            <span v-if="report.median_idle_gap_minutes !== null" class="hidden sm:flex items-center gap-1.5 text-xs text-ink-soft dark:text-night-text-soft"><Users :size="14" />{{ t('proactive.medianSilence', { value: formatGap(report.median_idle_gap_minutes) }) }}</span>
          </div>
          <div v-if="!report.openings.length" class="py-14 text-center text-sm text-ink-soft dark:text-night-text-soft">{{ t('proactive.noOpenings') }}</div>
          <div v-else class="divide-y divide-ink/10 dark:divide-night-text/10">
            <article v-for="opening in visibleOpenings" :key="opening.opening_id" class="py-6">
              <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <div class="flex items-center gap-2">
                  <span
                    class="inline-flex rounded-sm border px-1.5 py-0.5 font-sans text-[11px]"
                    :class="opening.initiator === 'friend' ? 'border-accent-soft text-accent dark:text-night-accent' : 'border-ink/20 text-ink-soft dark:border-night-text/20 dark:text-night-text-soft'"
                  >{{ opening.initiator === 'friend' ? t('proactive.friendInitiated') : t('proactive.selfInitiated') }}</span>
                  <time class="font-sans text-xs tabular-nums text-accent dark:text-night-accent">{{ formatOccurred(opening) }}</time>
                </div>
                <span class="text-xs text-ink-soft dark:text-night-text-soft">{{ openingTypeLabels[opening.opening_type] }} · {{ formatGap(opening.idle_gap_minutes) }}</span>
              </div>
              <p class="mt-3 text-base leading-7 whitespace-pre-line">{{ opening.content }}</p>
              <div v-if="opening.reason_category" class="mt-4 border-l-2 border-accent-soft/70 pl-4">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-medium">{{ reasonLabels[opening.reason_category] }}</span>
                  <span v-if="opening.reason_confidence !== null" class="font-sans text-xs tabular-nums text-ink-soft dark:text-night-text-soft">
                    {{ t('proactive.confidence', { value: Math.round(opening.reason_confidence * 100) }) }}
                  </span>
                </div>
                <p v-if="opening.reason_summary" class="mt-2 text-sm leading-6">{{ opening.reason_summary }}</p>
                <p v-if="opening.time_explanation" class="mt-2 text-sm leading-6 text-ink-soft dark:text-night-text-soft">
                  <span class="font-medium text-ink dark:text-night-text">{{ t('proactive.timeExplanation') }}</span>{{ opening.time_explanation }}
                </p>
                <blockquote
                  v-for="(evidence, evidenceIndex) in opening.reason_evidence"
                  :key="evidenceIndex"
                  class="mt-2 text-xs leading-5 text-ink-soft dark:text-night-text-soft"
                >
                  {{ t('proactive.evidence') }}{{ evidence.quote }}
                </blockquote>
                <p v-if="opening.reason_alternative_explanations.length" class="mt-2 text-xs leading-5 text-ink-soft dark:text-night-text-soft">
                  {{ t('proactive.alternatives') }}{{ opening.reason_alternative_explanations.join('；') }}
                </p>
              </div>
              <button
                v-if="opening.previous_tail || opening.response_excerpt"
                class="mt-3 inline-flex items-center gap-1.5 text-xs text-accent dark:text-night-accent"
                :aria-expanded="expanded.has(opening.opening_id)"
                @click="toggle(opening.opening_id)"
              >
                {{ expanded.has(opening.opening_id) ? t('proactive.collapse') : t('proactive.context') }}
                <ChevronUp v-if="expanded.has(opening.opening_id)" :size="14" />
                <ChevronDown v-else :size="14" />
              </button>
              <div v-if="expanded.has(opening.opening_id)" class="mt-3 space-y-2 border-l-2 border-accent-soft pl-3 text-sm leading-6 text-ink-soft dark:text-night-text-soft">
                <p v-if="opening.previous_tail"><span class="font-medium text-ink dark:text-night-text">{{ t('proactive.previous') }}</span>{{ opening.previous_tail }}</p>
                <p v-if="opening.response_excerpt"><span class="font-medium text-ink dark:text-night-text">{{ t('proactive.response') }}</span>{{ opening.response_excerpt }}</p>
              </div>
            </article>
          </div>
          <button
            v-if="hasMore"
            class="mt-5 inline-flex items-center gap-2 text-sm text-accent dark:text-night-accent hover:underline"
            @click="visibleLimit += 80"
          >
            {{ t('proactive.more', { count: report.openings.length - visibleLimit }) }}
          </button>
        </section>
      </template>
    </div>
  </div>
</template>
