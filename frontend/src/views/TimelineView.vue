<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Activity,
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ChevronUp,
  Circle,
  Coffee,
  DoorOpen,
  ExternalLink,
  Flag,
  Heart,
  HeartHandshake,
  MessageCircle,
  RefreshCw,
  Sparkles,
  Swords,
} from 'lucide-vue-next'
import { fetchTimeline } from '@/api/analysis'
import type { AnalysisEventType, TimelineEvent, TimelinePhase, TimelineReport } from '@/types/api'
import { useLocale } from '@/composables/useLocale'

const router = useRouter()
const { t, locale } = useLocale()
const report = ref<TimelineReport | null>(null)
const loading = ref(true)
const error = ref('')
const expanded = ref(new Set<string>())
const analysisGuideUrl = 'https://github.com/kldhsh123/Afterglow/wiki/高级与实验功能#聊天记录分析'

const iconByType = {
  milestone: Flag,
  conflict: Swords,
  reconciliation: HeartHandshake,
  intimacy: Heart,
  shared_activity: Sparkles,
  emotional_shift: Activity,
  separation: DoorOpen,
  daily: Coffee,
  other: Circle,
}

const labelByType = computed<Record<AnalysisEventType, string>>(() => ({
  milestone: t('timeline.types.milestone'),
  conflict: t('timeline.types.conflict'),
  reconciliation: t('timeline.types.reconciliation'),
  intimacy: t('timeline.types.intimacy'),
  shared_activity: t('timeline.types.shared_activity'),
  emotional_shift: t('timeline.types.emotional_shift'),
  separation: t('timeline.types.separation'),
  daily: t('timeline.types.daily'),
  other: t('timeline.types.other'),
}))

const phases = computed(() => {
  if (!report.value) return []
  const eventMap = new Map(report.value.events.map((event) => [event.event_id, event]))
  const assigned = new Set<string>()
  const groups = report.value.phases.map((phase) => {
    const events = phase.event_ids
      .map((id) => eventMap.get(id))
      .filter((event): event is TimelineEvent => Boolean(event))
    events.forEach((event) => assigned.add(event.event_id))
    return { phase, events }
  })
  const unassigned = report.value.events.filter((event) => !assigned.has(event.event_id))
  if (unassigned.length) {
    groups.push({
      phase: {
        title: t('timeline.other'),
        start_date: unassigned[0].date,
        end_date: unassigned[unassigned.length - 1].date,
        summary: '',
        event_ids: unassigned.map((event) => event.event_id),
      } satisfies TimelinePhase,
      events: unassigned,
    })
  }
  return groups
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    report.value = await fetchTimeline()
  } catch (reason) {
    error.value = `${t('errors.loadFailed')}: ${(reason as Error).message}`
  } finally {
    loading.value = false
  }
}

function toggle(eventId: string) {
  const next = new Set(expanded.value)
  next.has(eventId) ? next.delete(eventId) : next.add(eventId)
  expanded.value = next
}

function formatDate(value: string) {
  const parsed = new Date(`${value}T00:00:00`)
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat(locale.value, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      }).format(parsed)
}

onMounted(load)
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="max-w-4xl mx-auto px-4 py-6 sm:px-8 sm:py-10">
      <header class="flex items-center justify-between gap-4 border-b border-ink/15 dark:border-night-text/15 pb-5">
        <div class="flex items-center gap-2 min-w-0">
          <button
            class="p-2 -ml-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft dark:hover:text-night-text"
            :aria-label="t('timeline.back')"
            @click="router.push('/settings')"
          >
            <ChevronLeft :size="20" />
          </button>
          <div>
            <h1 class="text-xl font-medium">{{ t('timeline.title') }}</h1>
            <p v-if="report" class="text-xs mt-1 text-ink-soft dark:text-night-text-soft">
              {{ t('timeline.messageCount', { messages: report.source_message_count.toLocaleString(locale), events: report.events.length }) }}
            </p>
          </div>
        </div>
        <button
          class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft dark:hover:text-night-text"
          :title="t('timeline.refresh')"
          :aria-label="t('timeline.refreshLabel')"
          @click="load"
        >
          <RefreshCw :size="18" :class="{ 'animate-spin': loading }" />
        </button>
      </header>

      <div v-if="loading" class="min-h-72 flex items-center justify-center text-ink-soft dark:text-night-text-soft">
        <RefreshCw :size="20" class="animate-spin mr-2" /> {{ t('timeline.loading') }}
      </div>
      <div v-else-if="error" class="min-h-72 flex flex-col items-center justify-center text-center">
        <CalendarDays :size="30" class="mb-3 text-ink-soft dark:text-night-text-soft" />
        <p class="font-medium">{{ t('timeline.unavailable') }}</p>
        <p class="text-sm mt-2 max-w-md text-ink-soft dark:text-night-text-soft">{{ error }}</p>
        <a
          :href="analysisGuideUrl"
          target="_blank"
          rel="noopener"
          class="mt-4 inline-flex items-center gap-1.5 text-sm text-accent dark:text-night-accent hover:underline"
        >
          {{ t('timeline.guide') }} <ExternalLink :size="14" />
        </a>
      </div>
      <div v-else-if="!report?.events.length" class="min-h-72 flex flex-col items-center justify-center text-center">
        <MessageCircle :size="30" class="mb-3 text-ink-soft dark:text-night-text-soft" />
        <p>{{ t('timeline.noEvents') }}</p>
        <a
          :href="analysisGuideUrl"
          target="_blank"
          rel="noopener"
          class="mt-4 inline-flex items-center gap-1.5 text-sm text-accent dark:text-night-accent hover:underline"
        >
          {{ t('timeline.guide') }} <ExternalLink :size="14" />
        </a>
      </div>

      <div v-else class="pt-10 space-y-14">
        <section v-for="group in phases" :key="`${group.phase.title}-${group.phase.start_date}`">
          <div class="grid sm:grid-cols-[9rem_1fr] gap-3 sm:gap-8 mb-7">
            <p class="font-sans text-sm tabular-nums text-accent dark:text-night-accent">
              {{ group.phase.start_date }}<br class="hidden sm:block" />
              <span v-if="group.phase.end_date !== group.phase.start_date">{{ t('timeline.to') }} {{ group.phase.end_date }}</span>
            </p>
            <div>
              <h2 class="text-lg font-medium">{{ group.phase.title }}</h2>
              <p v-if="group.phase.summary" class="text-sm mt-1 leading-6 text-ink-soft dark:text-night-text-soft">
                {{ group.phase.summary }}
              </p>
            </div>
          </div>

          <ol class="relative sm:ml-[10rem] border-l border-accent-soft/50 dark:border-night-accent-soft/50">
            <li v-for="event in group.events" :key="event.event_id" class="relative pl-7 sm:pl-9 pb-9 last:pb-0">
              <span
                class="absolute -left-[15px] top-0 w-[30px] h-[30px] rounded-full bg-paper dark:bg-night-bg
                       border border-accent-soft dark:border-night-accent-soft flex items-center justify-center"
              >
                <component :is="iconByType[event.type]" :size="14" />
              </span>
              <article class="rounded-lg border border-ink/10 dark:border-night-text/10 bg-paper-soft/75 dark:bg-night-bg-soft/75 p-4 sm:p-5">
                <div class="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <time class="font-sans text-xs tabular-nums text-accent dark:text-night-accent">{{ formatDate(event.date) }}</time>
                    <h3 class="text-base font-medium mt-1">{{ event.title }}</h3>
                  </div>
                  <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft">
                    <span>{{ labelByType[event.type] }}</span>
                    <span class="flex gap-0.5" :title="t('timeline.importance', { value: event.importance })">
                      <i
                        v-for="level in 5"
                        :key="level"
                        class="block w-1 h-3 rounded-sm"
                        :class="level <= event.importance ? 'bg-accent dark:bg-night-accent' : 'bg-ink/10 dark:bg-night-text/10'"
                      />
                    </span>
                  </div>
                </div>
                <p v-if="event.summary" class="mt-3 text-sm leading-7 text-ink-soft dark:text-night-text-soft">
                  {{ event.summary }}
                </p>
                <button
                  v-if="event.evidence.length"
                  class="mt-3 inline-flex items-center gap-1 text-xs text-accent dark:text-night-accent"
                  @click="toggle(event.event_id)"
                >
                  {{ expanded.has(event.event_id) ? t('timeline.collapse') : t('timeline.viewEvidence', { value: event.evidence.length }) }}
                  <ChevronUp v-if="expanded.has(event.event_id)" :size="14" />
                  <ChevronDown v-else :size="14" />
                </button>
                <div v-if="expanded.has(event.event_id)" class="mt-3 border-t border-ink/10 dark:border-night-text/10 pt-3 space-y-3">
                  <blockquote
                    v-for="(evidence, index) in event.evidence"
                    :key="`${evidence.quote}-${index}`"
                    class="border-l-2 border-accent-soft pl-3 text-sm leading-6"
                  >
                    {{ evidence.quote }}
                    <footer v-if="evidence.date" class="mt-1 font-sans text-xs text-ink-soft dark:text-night-text-soft">
                      {{ evidence.date }}
                    </footer>
                  </blockquote>
                </div>
              </article>
            </li>
          </ol>
        </section>
      </div>
    </div>
  </div>
</template>
