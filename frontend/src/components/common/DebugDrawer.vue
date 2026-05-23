<script setup lang="ts">
import { computed, defineComponent, h, ref, watch } from 'vue'
import { requestProactiveTopic } from '@/api/chat'
import { getDebugConfig, getDebugStats } from '@/api/extra'
import { searchMemory } from '@/api/memory'
import { useChatStore } from '@/stores/chat'
import { useSettingsStore } from '@/stores/settings'
import type { MemorySearchResponse, MemorySource, ProactiveResponse } from '@/types/api'
import { Activity, Copy, Database, MessageSquareText, RefreshCw, Search, X } from 'lucide-vue-next'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const chat = useChatStore()
const settings = useSettingsStore()

const stats = ref<Record<string, unknown> | null>(null)
const config = ref<Record<string, unknown> | null>(null)
const searchResult = ref<MemorySearchResponse | null>(null)
const proactiveResult = ref<ProactiveResponse | null>(null)
const errorMsg = ref<string | null>(null)
const loading = ref(false)
const searchLoading = ref(false)
const proactiveLoading = ref(false)
const copied = ref(false)

const query = ref('')
const reason = ref('manual')
const privateContext = ref('')
const topicHint = ref('')

const lastUserText = computed(() => {
  const msg = [...chat.messages].reverse().find((m) => m.role === 'user' && m.content.trim())
  return msg?.content ?? ''
})

const database = computed(() => (stats.value?.database ?? {}) as Record<string, unknown>)
const dbByOperation = computed(() => (database.value.by_operation ?? {}) as Record<string, DbOpStats>)
const dbRecent = computed(() => ((database.value.recent ?? []) as DbRecord[]).slice().reverse())
const dbSlowest = computed(() => (database.value.slowest ?? []) as DbRecord[])
const calls = computed(() => (stats.value?.calls ?? {}) as Record<string, CallStats>)
const modelChain = computed(() => ((stats.value?.model_chain ?? []) as ModelCall[]).slice().reverse())
const life = computed(() => (stats.value?.life ?? {}) as LifeDebug)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    if (!query.value.trim()) query.value = lastUserText.value
    void refresh()
  },
)

async function refresh() {
  loading.value = true
  errorMsg.value = null
  try {
    const [s, c] = await Promise.all([getDebugStats(), getDebugConfig()])
    stats.value = s
    config.value = c
  } catch (e) {
    errorMsg.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function runSearch() {
  const q = query.value.trim()
  if (!q) {
    searchResult.value = { fused: [], response_pairs: [], friend_examples: [], dialogue_windows: [], recent_live: [], trace_id: '' }
    return
  }
  searchLoading.value = true
  errorMsg.value = null
  try {
    searchResult.value = await searchMemory(q, 12, chat.conversationId)
    await refresh()
  } catch (e) {
    errorMsg.value = (e as Error).message
  } finally {
    searchLoading.value = false
  }
}

async function runProactive() {
  proactiveLoading.value = true
  errorMsg.value = null
  try {
    proactiveResult.value = await requestProactiveTopic(chat.conversationId, reason.value, {
      private_context: privateContext.value,
      topic_hint: topicHint.value,
    })
    await refresh()
  } catch (e) {
    errorMsg.value = (e as Error).message
  } finally {
    proactiveLoading.value = false
  }
}

async function copyDiagnosticPack() {
  const payload = {
    at: new Date().toISOString(),
    conversation_id: chat.conversationId,
    query: query.value,
    last_messages: chat.messages.slice(-8).map((m) => ({
      role: m.role,
      content: m.content,
      trace_id: m.traceId,
      image_count: m.images?.length ?? 0,
      memory_sources: m.memorySources?.length ?? 0,
      pending: Boolean(m.pending),
    })),
    search_result: searchResult.value,
    proactive_result: proactiveResult.value,
    stats: stats.value,
    config: config.value,
  }
  await navigator.clipboard.writeText(JSON.stringify(payload, null, 2))
  copied.value = true
  setTimeout(() => (copied.value = false), 1200)
}

function close() {
  emit('close')
}

interface DbRecord {
  ts_ms: number
  op: string
  table: string
  latency_ms: number
  rows: number
  status: string
  detail: string
}

interface DbOpStats {
  count: number
  error_count: number
  error_rate: number
  avg_latency_ms: number
  p50_latency_ms: number
  p95_latency_ms: number
  max_latency_ms: number
  rows: number
}

interface CallStats {
  count: number
  error_count: number
  error_rate: number
  avg_latency_ms: number
  p50_latency_ms: number
  p95_latency_ms: number
  last?: { ts_ms: number; latency_ms: number; status: string; detail: string }[]
}

interface ModelCall {
  ts_ms: number
  trace_id: string
  stage: string
  attempt: number
  model: string
  url: string
  stream: boolean
  latency_ms: number
  status: string
  status_code?: number
  upstream_request_id?: string
  request: Record<string, unknown>
  response: Record<string, unknown>
  error?: string
}

interface LifeSnapshot {
  date?: string
  time_slot?: string
  current_activity?: string
  recent_meal?: string
  mood?: string
  topic_seed?: string
  availability?: string
  next_update_at?: string
  reply_delay_seconds?: number
  reply_delay_reason?: string
  current_event_id?: string
  day_plan_summary?: string
  recent_timeline_summary?: string
}

interface LifeDebug {
  state_file?: string
  state_file_exists?: boolean
  snapshot?: LifeSnapshot
  model_decision?: Record<string, unknown>
  plan_decided_by_model?: boolean
  daily_plan?: Record<string, unknown>[]
  recent_timeline?: Record<string, unknown>[]
}

const MemoryGroup = defineComponent({
  props: {
    title: { type: String, required: true },
    items: { type: Array as () => MemorySource[], required: true },
  },
  setup(props) {
    return () =>
      h('div', { class: 'space-y-2' }, [
        h('div', { class: 'text-sm font-medium' }, `${props.title} (${props.items.length})`),
        props.items.length === 0
          ? h('div', { class: 'text-xs text-ink-soft dark:text-night-text-soft' }, '无结果')
          : h(
              'div',
              { class: 'space-y-2' },
              props.items.slice(0, 8).map((item) =>
                h('div', { class: 'debug-hit' }, [
                  h('div', { class: 'flex items-center justify-between gap-2 text-xs' }, [
                    h('span', { class: 'font-medium' }, `${item.kind} · rank ${item.rank}`),
                    h('span', { class: 'text-ink-soft dark:text-night-text-soft' }, `score ${item.score.toFixed(4)}`),
                  ]),
                  h('div', { class: 'text-xs text-ink-soft dark:text-night-text-soft' }, `${item.source || 'history'} · ${item.chunk_id}`),
                  h('pre', { class: 'mt-1 whitespace-pre-wrap break-words text-xs' }, item.text),
                ]),
              ),
            ),
      ])
  },
})

const LogList = defineComponent({
  props: {
    title: { type: String, required: true },
    items: { type: Array as () => DbRecord[], required: true },
  },
  setup(props) {
    return () =>
      h('div', { class: 'space-y-2' }, [
        h('div', { class: 'text-sm font-medium' }, `${props.title} (${props.items.length})`),
        props.items.length === 0
          ? h('div', { class: 'text-xs text-ink-soft dark:text-night-text-soft' }, '暂无记录')
          : h(
              'div',
              { class: 'space-y-1' },
              props.items.slice(0, 12).map((item) =>
                h('div', { class: 'debug-log' }, [
                  h('span', { class: item.status === 'error' ? 'text-warning' : '' }, `${item.op}:${item.table}`),
                  h('span', null, `${item.latency_ms}ms`),
                  h('span', null, `rows ${item.rows}`),
                  item.detail ? h('span', { class: 'text-ink-soft dark:text-night-text-soft' }, item.detail) : null,
                ]),
              ),
            ),
      ])
  },
})

const ModelChainList = defineComponent({
  props: {
    items: { type: Array as () => ModelCall[], required: true },
  },
  setup(props) {
    return () =>
      h('div', { class: 'space-y-2' }, [
        props.items.length === 0
          ? h('div', { class: 'text-xs text-ink-soft dark:text-night-text-soft' }, '暂无模型请求')
          : h(
              'div',
              { class: 'space-y-2' },
              props.items.slice(0, 20).map((item) =>
                h('div', { class: 'debug-hit space-y-1' }, [
                  h('div', { class: 'flex items-center justify-between gap-2 text-xs' }, [
                    h('span', { class: item.status === 'error' ? 'text-warning font-medium' : 'font-medium' }, `${item.stage} · ${item.model}`),
                    h('span', { class: 'text-ink-soft dark:text-night-text-soft' }, `${item.latency_ms}ms · attempt ${item.attempt}`),
                  ]),
                  h('div', { class: 'grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs' }, [
                    h('span', { class: 'text-ink-soft dark:text-night-text-soft' }, 'trace'),
                    h('span', { class: 'truncate' }, item.trace_id || '-'),
                    h('span', { class: 'text-ink-soft dark:text-night-text-soft' }, 'url'),
                    h('span', { class: 'truncate' }, item.url),
                    h('span', { class: 'text-ink-soft dark:text-night-text-soft' }, 'status'),
                    h('span', null, `${item.status}${item.status_code ? ` · HTTP ${item.status_code}` : ''}${item.stream ? ' · stream' : ''}`),
                    item.upstream_request_id
                      ? [
                          h('span', { class: 'text-ink-soft dark:text-night-text-soft' }, 'upstream'),
                          h('span', { class: 'truncate' }, item.upstream_request_id),
                        ]
                      : null,
                  ]),
                  item.error ? h('div', { class: 'text-xs text-warning' }, item.error) : null,
                  h('details', { class: 'text-xs' }, [
                    h('summary', { class: 'cursor-pointer text-ink-soft dark:text-night-text-soft' }, 'request / response 摘要'),
                    h('pre', { class: 'debug-pre mt-2' }, JSON.stringify({ request: item.request, response: item.response }, null, 2)),
                  ]),
                ]),
              ),
            ),
      ])
  },
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50">
      <button class="absolute inset-0 bg-ink/20 dark:bg-black/40" aria-label="关闭调试面板" @click="close" />
      <aside
        class="absolute right-0 top-0 h-full w-full max-w-2xl overflow-y-auto
               bg-paper dark:bg-night-bg text-ink dark:text-night-text
               border-l border-ink/10 dark:border-night-text/10 shadow-letter"
      >
        <header class="sticky top-0 z-10 bg-paper/95 dark:bg-night-bg/95 backdrop-blur px-5 py-4 border-b border-ink/10 dark:border-night-text/10">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h2 class="text-base font-medium">调试面板</h2>
              <p class="text-xs text-ink-soft dark:text-night-text-soft">conversation: {{ chat.conversationId }}</p>
            </div>
            <div class="flex items-center gap-1">
              <button class="debug-icon" title="刷新运行时统计" :disabled="loading" @click="refresh">
                <RefreshCw :size="17" :class="loading ? 'animate-spin' : ''" />
              </button>
              <button class="debug-icon" title="复制诊断包" @click="copyDiagnosticPack">
                <Copy :size="17" />
              </button>
              <button class="debug-icon" title="关闭" @click="close">
                <X :size="18" />
              </button>
            </div>
          </div>
          <p v-if="copied" class="mt-2 text-xs text-accent dark:text-night-accent">诊断包已复制</p>
          <p v-if="errorMsg" class="mt-2 text-xs text-warning">{{ errorMsg }}</p>
        </header>

        <main class="p-5 space-y-5">
          <section class="debug-section">
            <div class="debug-title"><Search :size="16" />检索调试</div>
            <textarea
              v-model="query"
              class="debug-textarea"
              rows="3"
              placeholder="输入要调试的检索 query"
            />
            <button class="debug-button" :disabled="searchLoading" @click="runSearch">
              {{ searchLoading ? '检索中...' : '运行 /memory/search' }}
            </button>
            <div v-if="searchResult" class="space-y-3">
              <p v-if="searchResult.trace_id" class="text-xs text-ink-soft dark:text-night-text-soft">
                trace_id: {{ searchResult.trace_id }}
              </p>
              <MemoryGroup title="融合结果 fused" :items="searchResult.fused" />
              <MemoryGroup title="问答响应 response_pairs" :items="searchResult.response_pairs || []" />
              <MemoryGroup title="朋友单条 friend_examples" :items="searchResult.friend_examples" />
              <MemoryGroup title="对话窗口 dialogue_windows" :items="searchResult.dialogue_windows" />
              <MemoryGroup title="最近对话 recent_live（不参与 fused）" :items="searchResult.recent_live || []" />
            </div>
          </section>

          <section class="debug-section">
            <div class="debug-title"><Database :size="16" />数据库性能</div>
            <div class="grid grid-cols-2 gap-2">
              <div v-for="(item, key) in dbByOperation" :key="key" class="debug-metric">
                <div class="font-medium truncate">{{ key }}</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft">
                  count {{ item.count }} · rows {{ item.rows }} · err {{ Math.round(item.error_rate * 100) }}%
                </div>
                <div class="text-xs">
                  avg {{ item.avg_latency_ms }}ms · p95 {{ item.p95_latency_ms }}ms · max {{ item.max_latency_ms }}ms
                </div>
              </div>
            </div>
            <LogList title="最慢 DB 操作" :items="dbSlowest" />
            <LogList title="最近 DB 操作" :items="dbRecent" />
          </section>

          <section class="debug-section">
            <div class="debug-title"><Activity :size="16" />模型与链路耗时</div>
            <div class="grid grid-cols-2 gap-2">
              <div v-for="(item, key) in calls" :key="key" class="debug-metric">
                <div class="font-medium truncate">{{ key }}</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft">
                  count {{ item.count }} · err {{ Math.round(item.error_rate * 100) }}%
                </div>
                <div class="text-xs">
                  avg {{ item.avg_latency_ms }}ms · p95 {{ item.p95_latency_ms }}ms
                </div>
              </div>
            </div>
          </section>

          <section class="debug-section">
            <div class="debug-title"><Activity :size="16" />模型请求完整链路</div>
            <ModelChainList :items="modelChain" />
          </section>

          <section class="debug-section">
            <div class="debug-title"><Activity :size="16" />AI生活状态与决策</div>
            <div class="grid grid-cols-2 gap-2">
              <div class="debug-metric">
                <div class="font-medium">当前状态</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft">
                  {{ life.snapshot?.date || '-' }} · {{ life.snapshot?.time_slot || '-' }}
                </div>
                <div class="text-sm">{{ life.snapshot?.current_activity || '-' }}</div>
              </div>
              <div class="debug-metric">
                <div class="font-medium">可用与延迟</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft">
                  {{ life.snapshot?.availability || '-' }} · delay {{ life.snapshot?.reply_delay_seconds ?? 0 }}s
                </div>
                <div class="text-sm">{{ life.snapshot?.reply_delay_reason || '无延迟原因' }}</div>
              </div>
              <div class="debug-metric">
                <div class="font-medium">吃喝 / 心情</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft">
                  {{ life.snapshot?.recent_meal || '-' }}
                </div>
                <div class="text-sm">{{ life.snapshot?.mood || '-' }}</div>
              </div>
              <div class="debug-metric">
                <div class="font-medium">下一次更新</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft">
                  event {{ life.snapshot?.current_event_id || '-' }}
                </div>
                <div class="text-sm">{{ life.snapshot?.next_update_at || '-' }}</div>
              </div>
            </div>
            <div class="debug-metric">
              <div class="font-medium">可自然展开的话题</div>
              <div class="text-sm">{{ life.snapshot?.topic_seed || '-' }}</div>
            </div>
            <div class="text-xs text-ink-soft dark:text-night-text-soft">
              state: {{ life.state_file || '-' }} · plan_by_model: {{ life.plan_decided_by_model ? 'yes' : 'no' }}
            </div>
            <div class="debug-metric space-y-2">
              <div class="flex items-center justify-between gap-3">
                <div>
                  <div class="font-medium">本地强制回复延迟（调试）</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft">
                    勾选后忽略后端 reply_delay_seconds，用下方秒数强制延迟；仅本地生效，不改后端决策。
                  </div>
                </div>
                <label class="inline-flex items-center gap-2 text-xs whitespace-nowrap">
                  <input
                    type="checkbox"
                    :checked="settings.debugForceReplyDelay"
                    @change="(e) => (settings.debugForceReplyDelay = (e.target as HTMLInputElement).checked)"
                  />
                  启用覆盖
                </label>
              </div>
              <div class="flex items-center gap-2">
                <input
                  type="number"
                  min="0"
                  max="120"
                  step="1"
                  class="debug-input w-28"
                  :value="settings.debugReplyDelaySeconds"
                  :disabled="!settings.debugForceReplyDelay"
                  @input="(e) => {
                    const v = Number((e.target as HTMLInputElement).value)
                    settings.debugReplyDelaySeconds = Number.isFinite(v) ? Math.max(0, Math.min(120, v)) : 0
                  }"
                />
                <span class="text-xs text-ink-soft dark:text-night-text-soft">秒（0-120，前端硬上限 120s）</span>
              </div>
            </div>
            <details class="text-xs">
              <summary class="cursor-pointer text-ink-soft dark:text-night-text-soft">本轮/上一轮模型决策 current</summary>
              <pre class="debug-pre mt-2">{{ JSON.stringify(life.model_decision || {}, null, 2) }}</pre>
            </details>
            <details class="text-xs">
              <summary class="cursor-pointer text-ink-soft dark:text-night-text-soft">今日计划 daily_plan</summary>
              <pre class="debug-pre mt-2">{{ JSON.stringify(life.daily_plan || [], null, 2) }}</pre>
            </details>
            <details class="text-xs">
              <summary class="cursor-pointer text-ink-soft dark:text-night-text-soft">最近时间线 timeline</summary>
              <pre class="debug-pre mt-2">{{ JSON.stringify(life.recent_timeline || [], null, 2) }}</pre>
            </details>
          </section>

          <section class="debug-section">
            <div class="debug-title">最近消息 Trace</div>
            <div class="space-y-1">
              <div
                v-for="m in chat.messages.slice(-8)"
                :key="m.id"
                class="debug-log grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)]"
              >
                <span>{{ m.role }}</span>
                <span class="truncate">{{ m.traceId || '-' }}</span>
                <span class="truncate text-ink-soft dark:text-night-text-soft">{{ m.content }}</span>
              </div>
            </div>
          </section>

          <section class="debug-section">
            <div class="debug-title"><MessageSquareText :size="16" />主动话题测试</div>
            <div class="grid sm:grid-cols-3 gap-2">
              <input v-model="reason" class="debug-input" placeholder="reason" />
              <input v-model="topicHint" class="debug-input sm:col-span-2" placeholder="topic_hint" />
            </div>
            <textarea v-model="privateContext" class="debug-textarea" rows="2" placeholder="private_context，不作为用户消息写入历史" />
            <button class="debug-button" :disabled="proactiveLoading" @click="runProactive">
              {{ proactiveLoading ? '生成中...' : '调用 /v1/companion/proactive' }}
            </button>
            <pre v-if="proactiveResult" class="debug-pre">{{ JSON.stringify(proactiveResult, null, 2) }}</pre>
          </section>

          <section class="debug-section">
            <div class="debug-title">运行配置快照</div>
            <pre class="debug-pre max-h-80">{{ JSON.stringify(config, null, 2) }}</pre>
          </section>
        </main>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.debug-icon {
  @apply p-2 rounded-lg text-ink-soft hover:text-ink dark:text-night-text-soft dark:hover:text-night-text disabled:opacity-50;
}
.debug-section {
  @apply space-y-3 rounded-xl border border-ink/10 dark:border-night-text/10 bg-paper-soft dark:bg-night-bg-soft p-4;
}
.debug-title {
  @apply flex items-center gap-2 text-sm font-medium;
}
.debug-button {
  @apply inline-flex items-center justify-center rounded-lg px-3 py-2 text-sm bg-accent text-paper dark:bg-night-accent dark:text-night-bg disabled:opacity-50;
}
.debug-input,
.debug-textarea {
  @apply w-full rounded-lg border border-ink/10 dark:border-night-text/10 bg-paper dark:bg-night-bg px-3 py-2 text-sm outline-none focus:border-accent dark:focus:border-night-accent;
}
.debug-pre {
  @apply overflow-auto rounded-lg bg-paper dark:bg-night-bg p-3 text-xs;
}
.debug-metric,
.debug-hit {
  @apply rounded-lg bg-paper dark:bg-night-bg p-3 border border-ink/5 dark:border-night-text/10;
}
.debug-log {
  @apply grid grid-cols-[minmax(0,1fr)_auto_auto_minmax(0,1fr)] gap-2 rounded-lg bg-paper dark:bg-night-bg px-3 py-2 text-xs;
}
</style>
