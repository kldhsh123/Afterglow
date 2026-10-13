<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  BookOpenText,
  ChevronLeft,
  Eye,
  EyeOff,
  ExternalLink,
  LockKeyhole,
  RefreshCw,
  ShieldAlert,
  X,
} from 'lucide-vue-next'
import { fetchExperimentalReport, fetchPersonalityReport } from '@/api/analysis'
import { renderDocumentMarkdown } from '@/composables/markdown'
import { useSettingsStore } from '@/stores/settings'
import type {
  ExperimentalCategory,
  ExperimentalReport,
  PersonalityReport,
} from '@/types/api'
import { useLocale } from '@/composables/useLocale'

const router = useRouter()
const { t, locale } = useLocale()
const settings = useSettingsStore()
const report = ref<PersonalityReport | null>(null)
const experimental = ref<ExperimentalReport | null>(null)
const loading = ref(true)
const experimentalLoading = ref(false)
const error = ref('')
const experimentalError = ref('')
const showDisclaimer = ref(false)
const consentChecked = ref(false)
const analysisGuideUrl = 'https://github.com/kldhsh123/Afterglow/wiki/高级与实验功能#聊天记录分析'

const categoryLabels = computed<Record<ExperimentalCategory, string>>(() => ({
  personality_hypothesis: t('report.categories.personality_hypothesis'), interpersonal_style: t('report.categories.interpersonal_style'), attachment: t('report.categories.attachment'),
  deception_pattern: t('report.categories.deception_pattern'), manipulation_intent: t('report.categories.manipulation_intent'), mental_health_hypothesis: t('report.categories.mental_health_hypothesis'),
  manipulation_pattern: t('report.categories.manipulation_pattern'), internal_contradiction: t('report.categories.internal_contradiction'), wellbeing_signal: t('report.categories.wellbeing_signal'),
}))

const reportHtml = computed(() => {
  if (!report.value) return ''
  const lines: string[] = []
  if (report.value.summary) lines.push(`## ${t('report.overview')}`, '', report.value.summary, '')
  for (const section of report.value.sections) {
    lines.push(`## ${section.title}`, '')
    if (!section.observations.length) {
      lines.push(t('report.insufficient'), '')
      continue
    }
    for (const observation of section.observations) {
      lines.push(`### ${observation.claim}`, '')
      lines.push(`**${t('report.confidence', { value: Math.round(observation.confidence * 100) })}**`, '')
      observation.evidence.forEach((evidence) => {
        const quote = evidence.quote.replace(/\n/g, ' ')
        lines.push(`> ${quote}${evidence.date ? `（${evidence.date}）` : ''}`, '')
      })
      if (observation.counterexamples.length) {
        lines.push(`**${t('report.counterexample')}** ${observation.counterexamples.join('；')}`, '')
      }
      if (observation.alternative_explanations.length) {
        lines.push(`**${t('report.alternatives')}** ${observation.alternative_explanations.join('；')}`, '')
      }
    }
  }
  return renderDocumentMarkdown(lines.join('\n'))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    report.value = await fetchPersonalityReport()
  } catch (reason) {
    error.value = (reason as Error).message
  } finally {
    loading.value = false
  }
}

async function loadExperimental() {
  if (experimental.value || experimentalLoading.value) return
  experimentalLoading.value = true
  experimentalError.value = ''
  try {
    experimental.value = await fetchExperimentalReport()
  } catch (reason) {
    experimentalError.value = (reason as Error).message
  } finally {
    experimentalLoading.value = false
  }
}

function requestExperimental() {
  if (settings.experimentalDisclaimerAccepted) {
    settings.experimentalAnalysisVisible = true
    return
  }
  consentChecked.value = false
  showDisclaimer.value = true
}

function acceptDisclaimer() {
  if (!consentChecked.value) return
  settings.experimentalDisclaimerAccepted = true
  settings.experimentalAnalysisVisible = true
  showDisclaimer.value = false
}

watch(
  () => settings.experimentalAnalysisVisible,
  (visible) => {
    if (visible) void loadExperimental()
  },
  { immediate: true },
)

onMounted(load)
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="max-w-3xl mx-auto px-4 py-6 sm:px-8 sm:py-10">
      <header class="flex items-center justify-between gap-4 border-b border-ink/15 dark:border-night-text/15 pb-5">
        <div class="flex items-center gap-2">
          <button
            class="p-2 -ml-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft dark:hover:text-night-text"
            :aria-label="t('report.back')"
            @click="router.push('/settings')"
          >
            <ChevronLeft :size="20" />
          </button>
          <div>
            <h1 class="text-xl font-medium">{{ t('report.title') }}</h1>
            <p v-if="report" class="mt-1 font-sans text-xs tabular-nums text-ink-soft dark:text-night-text-soft">
              {{ t('report.generated', { date: new Date(report.generated_at).toLocaleDateString(locale) }) }}
            </p>
          </div>
        </div>
        <button
          class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft dark:hover:text-night-text"
          :title="t('report.refresh')"
          :aria-label="t('report.refreshLabel')"
          @click="load"
        >
          <RefreshCw :size="18" :class="{ 'animate-spin': loading }" />
        </button>
      </header>

      <div v-if="loading" class="min-h-72 flex items-center justify-center text-ink-soft dark:text-night-text-soft">
        <RefreshCw :size="20" class="animate-spin mr-2" /> {{ t('report.loading') }}
      </div>
      <div v-else-if="error" class="min-h-72 flex flex-col items-center justify-center text-center">
        <BookOpenText :size="30" class="mb-3 text-ink-soft dark:text-night-text-soft" />
        <p class="font-medium">{{ t('report.unavailable') }}</p>
        <p class="text-sm mt-2 max-w-md text-ink-soft dark:text-night-text-soft">{{ error }}</p>
        <a
          :href="analysisGuideUrl"
          target="_blank"
          rel="noopener"
          class="mt-4 inline-flex items-center gap-1.5 text-sm text-accent dark:text-night-accent hover:underline"
        >
          {{ t('report.guide') }} <ExternalLink :size="14" />
        </a>
      </div>

      <template v-else-if="report">
        <aside class="my-8 border-l-2 border-accent-soft pl-4 text-sm leading-6 text-ink-soft dark:text-night-text-soft">
          {{ report.disclaimer }}
        </aside>
        <article class="analysis-document" v-html="reportHtml" />

        <section class="mt-14 pt-7 border-t border-ink/15 dark:border-night-text/15">
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="flex items-start gap-3 max-w-xl">
              <ShieldAlert :size="20" class="mt-0.5 shrink-0 text-warning" />
              <div>
                <h2 class="text-base font-medium">{{ t('report.experimental') }}</h2>
                <p class="mt-1 text-sm leading-6 text-ink-soft dark:text-night-text-soft">
                  {{ t('report.experimentalHint') }}
                </p>
              </div>
            </div>
            <button
              v-if="!settings.experimentalAnalysisVisible"
              class="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-ink/15 dark:border-night-text/15 text-sm"
              @click="requestExperimental"
            >
              <Eye :size="16" /> {{ t('report.view') }}
            </button>
            <button
              v-else
              class="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-ink/15 dark:border-night-text/15 text-sm"
              @click="settings.experimentalAnalysisVisible = false"
            >
              <EyeOff :size="16" /> {{ t('report.hide') }}
            </button>
          </div>

          <div v-if="!settings.experimentalAnalysisVisible" class="mt-6 min-h-28 flex items-center justify-center border border-dashed border-ink/15 dark:border-night-text/15 rounded-lg">
            <div class="text-center text-ink-soft dark:text-night-text-soft">
              <LockKeyhole :size="20" class="mx-auto mb-2" />
              <p class="text-sm">{{ t('report.hidden') }}</p>
            </div>
          </div>
          <div v-else-if="experimentalLoading" class="mt-6 py-12 flex items-center justify-center text-sm text-ink-soft dark:text-night-text-soft">
            <RefreshCw :size="17" class="animate-spin mr-2" /> {{ t('report.experimentalLoading') }}
          </div>
          <div v-else-if="experimentalError" class="mt-6 text-sm text-warning">
            <p>{{ experimentalError }}</p>
            <a
              :href="analysisGuideUrl"
              target="_blank"
              rel="noopener"
              class="mt-2 inline-flex items-center gap-1.5 text-accent dark:text-night-accent hover:underline"
            >
              {{ t('report.guide') }} <ExternalLink :size="14" />
            </a>
          </div>
          <div v-else-if="experimental" class="mt-6 space-y-4">
            <p class="text-xs leading-5 text-warning">{{ experimental.disclaimer }}</p>
            <p v-if="experimental.summary" class="text-sm leading-7">{{ experimental.summary }}</p>
            <article
              v-for="(signal, index) in experimental.signals"
              :key="`${signal.category}-${index}`"
              class="rounded-lg border border-warning/20 bg-warning/5 p-4"
            >
              <div class="flex items-center justify-between gap-3">
                <h3 class="text-sm font-medium">{{ categoryLabels[signal.category] }}</h3>
                <span class="font-sans text-xs tabular-nums text-ink-soft dark:text-night-text-soft">
                  {{ t('report.confidenceShort', { value: Math.round(signal.confidence * 100) }) }}
                </span>
              </div>
              <div class="mt-2 h-1 bg-ink/10 dark:bg-night-text/10 overflow-hidden rounded-sm">
                <div class="h-full bg-warning" :style="{ width: `${signal.confidence * 100}%` }" />
              </div>
              <p class="mt-3 text-sm leading-7">{{ signal.claim }}</p>
              <p v-if="signal.inference_basis" class="mt-2 text-xs leading-5 text-ink-soft dark:text-night-text-soft">
                {{ t('report.basis') }}{{ signal.inference_basis }}
              </p>
              <p v-if="signal.conditions.length" class="mt-2 text-xs leading-5 text-ink-soft dark:text-night-text-soft">
                {{ t('report.conditions') }}{{ signal.conditions.join('；') }}
              </p>
              <blockquote
                v-for="(evidence, evidenceIndex) in signal.evidence"
                :key="evidenceIndex"
                class="mt-3 border-l-2 border-warning/40 pl-3 text-sm leading-6 text-ink-soft dark:text-night-text-soft"
              >
                {{ evidence.quote }}
              </blockquote>
              <p class="mt-3 text-xs leading-5 text-ink-soft dark:text-night-text-soft">
                {{ t('report.alternatives') }}{{ signal.alternative_explanations.join('；') }}
              </p>
              <p v-if="signal.counterexamples.length" class="mt-2 text-xs leading-5 text-ink-soft dark:text-night-text-soft">
                {{ t('report.counterexample') }}{{ signal.counterexamples.join('；') }}
              </p>
              <p v-if="signal.category === 'wellbeing_signal' || signal.category === 'mental_health_hypothesis'" class="mt-2 text-xs leading-5 text-warning">
                {{ t('report.notDiagnosis') }}
              </p>
            </article>
            <p v-if="!experimental.signals.length" class="text-sm text-ink-soft dark:text-night-text-soft">
              {{ t('report.noSignals') }}
            </p>
          </div>
        </section>
      </template>
    </div>

    <div v-if="showDisclaimer" class="fixed inset-0 z-50 bg-ink/45 dark:bg-black/60 flex items-center justify-center p-4" @click.self="showDisclaimer = false">
      <section class="w-full max-w-lg rounded-lg bg-paper-soft dark:bg-night-bg-soft shadow-letter-strong border border-ink/10 dark:border-night-text/10 p-5 sm:p-6">
        <div class="flex items-start justify-between gap-4">
          <div class="flex items-center gap-2">
            <ShieldAlert :size="20" class="text-warning" />
            <h2 class="text-lg font-medium">{{ t('report.confirmTitle') }}</h2>
          </div>
          <button class="p-1 text-ink-soft dark:text-night-text-soft" :aria-label="t('report.close')" @click="showDisclaimer = false">
            <X :size="18" />
          </button>
        </div>
        <div class="mt-4 space-y-3 text-sm leading-6 text-ink-soft dark:text-night-text-soft">
          <p>{{ t('report.privacy') }}</p>
          <p>{{ t('report.limits') }}</p>
        </div>
        <label class="mt-5 flex items-start gap-3 text-sm cursor-pointer">
          <input v-model="consentChecked" type="checkbox" class="mt-1" />
          <span>{{ t('report.consent') }}</span>
        </label>
        <button
          :disabled="!consentChecked"
          class="mt-5 w-full py-2 rounded-lg bg-ink text-paper dark:bg-night-accent dark:text-night-bg disabled:opacity-40"
          @click="acceptDisclaimer"
        >
          {{ t('report.confirm') }}
        </button>
      </section>
    </div>
  </div>
</template>

<style scoped>
.analysis-document :deep(h2) {
  margin-top: 2.5rem;
  padding-bottom: 0.65rem;
  border-bottom: 1px solid rgb(26 47 75 / 0.12);
  font-size: 1.125rem;
  font-weight: 500;
}

.analysis-document :deep(h3) {
  margin-top: 1.75rem;
  font-size: 1rem;
  font-weight: 500;
  line-height: 1.75;
}

.analysis-document :deep(p) {
  margin-top: 0.75rem;
  font-size: 0.9rem;
  line-height: 1.8;
  color: rgb(74 93 122);
}

.analysis-document :deep(blockquote) {
  margin-top: 0.75rem;
  border-left: 2px solid rgb(196 154 108);
  padding-left: 0.85rem;
  font-size: 0.9rem;
  line-height: 1.7;
}

:global(.dark) .analysis-document :deep(h2) {
  border-color: rgb(230 234 239 / 0.12);
}

:global(.dark) .analysis-document :deep(p) {
  color: rgb(156 166 179);
}
</style>
