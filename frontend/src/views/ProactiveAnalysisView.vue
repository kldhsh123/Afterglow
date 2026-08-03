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

const router = useRouter()
const report = ref<ProactiveAnalysisReport | null>(null)
const loading = ref(true)
const error = ref('')
const visibleLimit = ref(80)
const expanded = ref(new Set<string>())

const weekdayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const openingTypeLabels: Record<ProactiveOpeningType, string> = {
  greeting: '问候',
  life_check: '近况询问',
  care: '关心',
  continue_topic: '延续话题',
  self_share: '自我分享',
  playful: '玩笑',
  affection: '亲昵',
  wake_ping: '叫醒/早安',
  short_ping: '短 ping',
  question_probe: '提问',
  night_ping: '深夜问候',
  other: '其他',
}
const reasonLabels: Record<ProactiveReasonCategory, string> = {
  continue_topic: '延续话题',
  event_trigger: '事件触发',
  care: '关心近况',
  self_share: '分享事情',
  question: '询问信息',
  emotional_need: '情绪或陪伴需要',
  routine: '固定习惯',
  greeting: '日常问候',
  playful: '玩笑互动',
  affection: '亲昵互动',
  other: '其他原因',
  unknown: '原因不明确',
}

const hourMax = computed(() => Math.max(1, ...(report.value?.hour_counts ?? [1])))
const weekdayMax = computed(() => Math.max(1, ...(report.value?.weekday_counts ?? [1])))
const monthMax = computed(() => Math.max(1, ...(report.value?.monthly_counts.map((item) => item.count) ?? [1])))
const topHour = computed(() => {
  const counts = report.value?.hour_counts ?? []
  const index = counts.indexOf(Math.max(...counts))
  return index >= 0 && counts[index] > 0 ? `${index}:00` : '暂无'
})
const topWeekday = computed(() => {
  const counts = report.value?.weekday_counts ?? []
  const index = counts.indexOf(Math.max(...counts))
  return index >= 0 && counts[index] > 0 ? weekdayLabels[index] : '暂无'
})
const visibleOpenings = computed(() => report.value?.openings.slice(0, visibleLimit.value) ?? [])
const hasMore = computed(() => Boolean(report.value && visibleLimit.value < report.value.openings.length))

async function load() {
  loading.value = true
  error.value = ''
  try {
    report.value = await fetchProactiveAnalysis()
  } catch (reason) {
    error.value = (reason as Error).message
  } finally {
    loading.value = false
  }
}

function formatOccurred(record: ProactiveOpeningRecord) {
  const date = new Date(record.occurred_at)
  if (Number.isNaN(date.getTime())) return record.occurred_at
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

function toggle(openingId: string) {
  const next = new Set(expanded.value)
  next.has(openingId) ? next.delete(openingId) : next.add(openingId)
  expanded.value = next
}

function formatGap(minutes: number | null) {
  if (minutes === null) return '记录起点，之前没有可计算的会话'
  if (minutes < 60) return `沉默 ${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `沉默 ${hours} 小时 ${rest} 分钟` : `沉默 ${hours} 小时`
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
            aria-label="返回设置"
            @click="router.push('/settings')"
          >
            <ChevronLeft :size="20" />
          </button>
          <div>
            <h1 class="text-xl font-medium">主动开聊分析</h1>
            <p v-if="report" class="mt-1 font-sans text-xs tabular-nums text-ink-soft dark:text-night-text-soft">
              {{ report.range_start }} 至 {{ report.range_end }} · {{ report.source_message_count.toLocaleString() }} 条消息
            </p>
          </div>
        </div>
        <button
          class="p-2 rounded-full text-ink-soft hover:text-ink dark:text-night-text-soft dark:hover:text-night-text"
          title="刷新"
          aria-label="刷新主动开聊分析"
          @click="load"
        >
          <RefreshCw :size="18" :class="{ 'animate-spin': loading }" />
        </button>
      </header>

      <div v-if="loading" class="min-h-72 flex items-center justify-center text-ink-soft dark:text-night-text-soft">
        <RefreshCw :size="20" class="animate-spin mr-2" /> 正在读取主动开聊分析
      </div>
      <div v-else-if="error" class="min-h-72 flex flex-col items-center justify-center text-center">
        <MessageCircle :size="30" class="mb-3 text-ink-soft dark:text-night-text-soft" />
        <p class="font-medium">主动开聊分析尚不可用</p>
        <p class="text-sm mt-2 max-w-md text-ink-soft dark:text-night-text-soft">{{ error }}</p>
        <p class="mt-3 text-xs text-ink-soft dark:text-night-text-soft">
          请先运行 xuwen analyze-proactive 生成主动开聊分析报告。
        </p>
      </div>
      <template v-else-if="report">
        <aside class="my-8 border-l-2 border-accent-soft pl-4 text-sm leading-6 text-ink-soft dark:text-night-text-soft">
          这里的“开聊”指一段新会话由谁先发送。时间和频率来自消息时间戳；开聊原因与时间解释由 AI 根据上一轮、开场和首条回应推断，不等同于当事人的真实动机。
        </aside>

        <div
          v-if="report.ai_analysis_status !== 'completed'"
          class="mb-8 border-y border-warning/30 py-3 text-sm text-warning"
        >
          AI 原因分析状态：{{ report.ai_analysis_status === 'partial' ? `已分析 ${report.ai_analyzed_count}/${report.opening_count} 次开场` : report.ai_analysis_status === 'failed' ? '本次模型分析失败，仍可查看基础统计' : '本次未启用 AI 原因分析' }}
        </div>

        <section class="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-5 py-7 border-y border-ink/15 dark:border-night-text/15">
          <div>
            <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft"><MessageCircle :size="15" />主动开聊</div>
            <div class="mt-2 text-3xl font-medium tabular-nums">{{ report.initiative_count.toLocaleString() }}</div>
            <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">次</div>
          </div>
          <div>
            <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft"><BarChart3 :size="15" />主动发起率</div>
            <div class="mt-2 text-3xl font-medium tabular-nums">{{ Math.round(report.initiative_rate * 100) }}%</div>
            <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">在有明确首发者的会话中</div>
          </div>
          <div>
            <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft"><CalendarClock :size="15" />平均频率</div>
            <div class="mt-2 text-3xl font-medium tabular-nums">{{ report.average_per_30_days }}</div>
            <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">每 30 天</div>
          </div>
          <div>
            <div class="flex items-center gap-2 text-xs text-ink-soft dark:text-night-text-soft"><Clock3 :size="15" />典型时段</div>
            <div class="mt-2 text-3xl font-medium tabular-nums">{{ topHour }}</div>
            <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">{{ topWeekday }}最常见</div>
          </div>
        </section>

        <section class="pt-10">
          <div class="flex items-end justify-between gap-4">
            <div>
              <h2 class="text-lg font-medium">一天中的来信时间</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">按对方主动开场的本地时间统计</p>
            </div>
            <span class="font-sans text-xs tabular-nums text-ink-soft dark:text-night-text-soft">{{ report.initiative_count }} 次</span>
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
                :title="`${hour}:00 · ${count} 次`"
              />
              <span v-if="hour % 3 === 0" class="absolute top-full mt-2 font-sans text-[10px] tabular-nums text-ink-soft dark:text-night-text-soft">{{ hour }}</span>
            </div>
          </div>
        </section>

        <section class="grid lg:grid-cols-[1fr_1fr] gap-10 pt-12">
          <div>
            <h2 class="text-lg font-medium">星期分布</h2>
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
            <h2 class="text-lg font-medium">按月变化</h2>
            <div v-if="report.monthly_counts.length" class="mt-5 space-y-3">
              <div v-for="item in report.monthly_counts" :key="item.period" class="grid grid-cols-[5rem_1fr_2.5rem] items-center gap-3 text-sm">
                <span class="font-sans tabular-nums">{{ item.period }}</span>
                <div class="h-2 bg-ink/8 dark:bg-night-text/10 rounded-sm overflow-hidden">
                  <div class="h-full bg-accent-soft dark:bg-night-accent-soft" :style="{ width: `${item.count / monthMax * 100}%` }" />
                </div>
                <span class="font-sans text-right tabular-nums text-xs text-ink-soft dark:text-night-text-soft">{{ item.count }}</span>
              </div>
            </div>
            <p v-else class="mt-5 text-sm text-ink-soft dark:text-night-text-soft">还没有主动开聊记录。</p>
          </div>
        </section>

        <section class="pt-12">
          <div class="flex items-end justify-between gap-4 border-b border-ink/15 dark:border-night-text/15 pb-4">
            <div>
              <h2 class="text-lg font-medium">双方开聊记录</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">双方共 {{ report.opening_count }} 次开场，按时间从早到晚排列</p>
            </div>
            <span v-if="report.median_idle_gap_minutes !== null" class="hidden sm:flex items-center gap-1.5 text-xs text-ink-soft dark:text-night-text-soft"><Users :size="14" />中位沉默 {{ formatGap(report.median_idle_gap_minutes) }}</span>
          </div>
          <div v-if="!report.openings.length" class="py-14 text-center text-sm text-ink-soft dark:text-night-text-soft">没有识别到由双方开启的会话。</div>
          <div v-else class="divide-y divide-ink/10 dark:divide-night-text/10">
            <article v-for="opening in visibleOpenings" :key="opening.opening_id" class="py-6">
              <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <div class="flex items-center gap-2">
                  <span
                    class="inline-flex rounded-sm border px-1.5 py-0.5 font-sans text-[11px]"
                    :class="opening.initiator === 'friend' ? 'border-accent-soft text-accent dark:text-night-accent' : 'border-ink/20 text-ink-soft dark:border-night-text/20 dark:text-night-text-soft'"
                  >{{ opening.initiator === 'friend' ? '对方先发' : '我方先发' }}</span>
                  <time class="font-sans text-xs tabular-nums text-accent dark:text-night-accent">{{ formatOccurred(opening) }}</time>
                </div>
                <span class="text-xs text-ink-soft dark:text-night-text-soft">{{ openingTypeLabels[opening.opening_type] }} · {{ formatGap(opening.idle_gap_minutes) }}</span>
              </div>
              <p class="mt-3 text-base leading-7 whitespace-pre-line">{{ opening.content }}</p>
              <div v-if="opening.reason_category" class="mt-4 border-l-2 border-accent-soft/70 pl-4">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-medium">{{ reasonLabels[opening.reason_category] }}</span>
                  <span v-if="opening.reason_confidence !== null" class="font-sans text-xs tabular-nums text-ink-soft dark:text-night-text-soft">
                    置信度 {{ Math.round(opening.reason_confidence * 100) }}%
                  </span>
                </div>
                <p v-if="opening.reason_summary" class="mt-2 text-sm leading-6">{{ opening.reason_summary }}</p>
                <p v-if="opening.time_explanation" class="mt-2 text-sm leading-6 text-ink-soft dark:text-night-text-soft">
                  <span class="font-medium text-ink dark:text-night-text">时间解释：</span>{{ opening.time_explanation }}
                </p>
                <blockquote
                  v-for="(evidence, evidenceIndex) in opening.reason_evidence"
                  :key="evidenceIndex"
                  class="mt-2 text-xs leading-5 text-ink-soft dark:text-night-text-soft"
                >
                  依据：{{ evidence.quote }}
                </blockquote>
                <p v-if="opening.reason_alternative_explanations.length" class="mt-2 text-xs leading-5 text-ink-soft dark:text-night-text-soft">
                  其他可能：{{ opening.reason_alternative_explanations.join('；') }}
                </p>
              </div>
              <button
                v-if="opening.previous_tail || opening.response_excerpt"
                class="mt-3 inline-flex items-center gap-1.5 text-xs text-accent dark:text-night-accent"
                @click="toggle(opening.opening_id)"
              >
                {{ expanded.has(opening.opening_id) ? '收起对话上下文' : '查看对话上下文' }}
                <ChevronUp v-if="expanded.has(opening.opening_id)" :size="14" />
                <ChevronDown v-else :size="14" />
              </button>
              <div v-if="expanded.has(opening.opening_id)" class="mt-3 space-y-2 border-l-2 border-accent-soft pl-3 text-sm leading-6 text-ink-soft dark:text-night-text-soft">
                <p v-if="opening.previous_tail"><span class="font-medium text-ink dark:text-night-text">上一轮最后一句：</span>{{ opening.previous_tail }}</p>
                <p v-if="opening.response_excerpt"><span class="font-medium text-ink dark:text-night-text">首条回应：</span>{{ opening.response_excerpt }}</p>
              </div>
            </article>
          </div>
          <button
            v-if="hasMore"
            class="mt-5 inline-flex items-center gap-2 text-sm text-accent dark:text-night-accent hover:underline"
            @click="visibleLimit += 80"
          >
            加载更多（剩余 {{ report.openings.length - visibleLimit }} 次）
          </button>
        </section>
      </template>
    </div>
  </div>
</template>
