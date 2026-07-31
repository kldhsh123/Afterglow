<script setup lang="ts">
// 设置面板
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useMemoryStore } from '@/stores/memory'
import {
  fetchInfo,
  fetchMemoryStats,
  pauseWriteback,
  resumeWriteback,
  triggerUpdateCheck,
} from '@/api/memory'
import { cancelAnalysis, fetchAnalysisTask, startAnalysis } from '@/api/analysis'
import type { AnalysisTask, AppInfo, UpdateInfo } from '@/types/api'
import {
  BookOpenText,
  ChevronLeft,
  ChevronRight,
  Play,
  RefreshCw,
  Route,
  Square,
  Sticker as StickerIcon,
} from 'lucide-vue-next'
import DiagnosticsPanel from '@/components/common/DiagnosticsPanel.vue'

const settings = useSettingsStore()
const memory = useMemoryStore()
const router = useRouter()
const info = ref<AppInfo | null>(null)
const updateInfo = ref<UpdateInfo | null>(null)
const checkingUpdate = ref(false)
const updateCheckHint = ref<string | null>(null)
const backendError = ref<string | null>(null)
const busy = ref(false)
const analysisTask = ref<AnalysisTask | null>(null)
const analysisStarting = ref(false)
const analysisError = ref<string | null>(null)
const analysisTaskKey = 'xuwen.analysis.task_id'
let analysisPollTimer: ReturnType<typeof setInterval> | null = null

const analysisRunning = computed(
  () => analysisTask.value?.status === 'pending' || analysisTask.value?.status === 'running',
)

onMounted(async () => {
  try {
    info.value = await fetchInfo()
    updateInfo.value = info.value.update
  } catch (e) {
    backendError.value = `无法连接后端：${(e as Error).message}`
  }
  try {
    memory.setStats(await fetchMemoryStats())
  } catch {
    /* 忽略错误 */
  }
  await resumeAnalysisTask()
})

onUnmounted(stopAnalysisPolling)

async function checkForUpdate() {
  if (checkingUpdate.value) return
  checkingUpdate.value = true
  updateCheckHint.value = null
  try {
    const next = await triggerUpdateCheck()
    updateInfo.value = next
    if (next.last_error) {
      updateCheckHint.value = `检查失败：${next.last_error}`
    } else if (next.is_outdated && next.latest_version) {
      updateCheckHint.value = `发现新版本 ${next.latest_version}`
    } else if (next.latest_version) {
      updateCheckHint.value = '已是最新版'
    } else {
      updateCheckHint.value = '检查完成'
    }
    // 3 秒后清掉一次性提示，留下面板里的静态字段
    setTimeout(() => {
      updateCheckHint.value = null
    }, 3000)
  } catch (e) {
    updateCheckHint.value = `检查出错：${(e as Error).message}`
  } finally {
    checkingUpdate.value = false
  }
}

function formatTimestamp(ms: number | null): string {
  if (!ms) return '尚未检查'
  const d = new Date(ms)
  return d.toLocaleString()
}

async function togglePause() {
  busy.value = true
  try {
    if (memory.stats?.writeback_paused) await resumeWriteback()
    else await pauseWriteback()
    memory.setStats(await fetchMemoryStats())
  } catch (e) {
    backendError.value = `操作失败：${(e as Error).message}`
  } finally {
    busy.value = false
  }
}

async function startRelationshipAnalysis() {
  if (analysisStarting.value || analysisRunning.value) return
  analysisStarting.value = true
  analysisError.value = null
  try {
    analysisTask.value = await startAnalysis()
    localStorage.setItem(analysisTaskKey, analysisTask.value.task_id)
    startAnalysisPolling()
  } catch (error) {
    analysisError.value = (error as Error).message
  } finally {
    analysisStarting.value = false
  }
}

async function cancelRelationshipAnalysis() {
  if (!analysisTask.value || !analysisRunning.value) return
  try {
    await cancelAnalysis(analysisTask.value.task_id)
    await refreshAnalysisTask()
  } catch (error) {
    analysisError.value = (error as Error).message
  }
}

async function resumeAnalysisTask() {
  const taskId = localStorage.getItem(analysisTaskKey)
  if (!taskId) return
  try {
    analysisTask.value = await fetchAnalysisTask(taskId)
    if (analysisRunning.value) startAnalysisPolling()
    else localStorage.removeItem(analysisTaskKey)
  } catch {
    localStorage.removeItem(analysisTaskKey)
  }
}

async function refreshAnalysisTask() {
  if (!analysisTask.value) return
  try {
    analysisTask.value = await fetchAnalysisTask(analysisTask.value.task_id)
    if (!analysisRunning.value) {
      stopAnalysisPolling()
      localStorage.removeItem(analysisTaskKey)
    }
  } catch (error) {
    stopAnalysisPolling()
    analysisError.value = (error as Error).message
  }
}

function startAnalysisPolling() {
  stopAnalysisPolling()
  analysisPollTimer = setInterval(() => {
    void refreshAnalysisTask()
  }, 1000)
}

function stopAnalysisPolling() {
  if (analysisPollTimer !== null) {
    clearInterval(analysisPollTimer)
    analysisPollTimer = null
  }
}

function back() {
  router.push('/')
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="max-w-2xl mx-auto px-4 py-6 sm:py-10 space-y-6">
      <header class="flex items-center gap-2">
        <button
          class="p-2 -ml-2 rounded-full text-ink-soft hover:text-ink
                 dark:text-night-text-soft dark:hover:text-night-text"
          @click="back"
        >
          <ChevronLeft :size="20" />
        </button>
        <h1 class="text-xl font-medium">设置</h1>
      </header>

      <!-- 后端连接 -->
      <section class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                      shadow-letter border border-ink/5 dark:border-night-text/10">
        <h2 class="text-base font-medium">后端连接</h2>
        <label class="block">
          <span class="text-sm text-ink-soft dark:text-night-text-soft">后端 API 地址</span>
          <input
            v-model="settings.backendBaseUrl"
            placeholder="留空使用 Vite 代理（dev）；生产填如 http://127.0.0.1:8000"
            class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                   border border-ink/10 dark:border-night-text/10 outline-none
                   focus:ring-2 focus:ring-accent-soft"
          />
        </label>
        <label class="block">
          <span class="text-sm text-ink-soft dark:text-night-text-soft">
            本地 API key（后端默认需要 XUWEN_API_KEY）
          </span>
          <input
            v-model="settings.localApiKey"
            type="password"
            class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                   border border-ink/10 dark:border-night-text/10 outline-none
                   focus:ring-2 focus:ring-accent-soft"
          />
        </label>
        <p v-if="backendError" class="text-sm text-warning">{{ backendError }}</p>
        <div v-if="info" class="text-xs text-ink-soft dark:text-night-text-soft space-y-1">
          <div>后端版本：{{ info.version }}</div>
          <div>对话模型：{{ info.chat_model }}</div>
          <div>向量模型：{{ info.embedding_model }}</div>
          <div>人格模板：{{ info.persona_template }} · {{ info.relationship_type }}</div>
          <div>persona 卡片：{{ info.has_persona_card ? '已生成' : '尚未生成（运行 analyze_persona.py）' }}</div>
        </div>
      </section>

      <!-- 版本与更新 -->
      <section class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                      shadow-letter border border-ink/5 dark:border-night-text/10">
        <div class="flex items-center justify-between">
          <h2 class="text-base font-medium">版本与更新</h2>
          <button
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs
                   text-ink-soft dark:text-night-text-soft
                   bg-paper/80 dark:bg-night-bg/80
                   border border-ink/10 dark:border-night-text/10
                   hover:text-ink dark:hover:text-night-text disabled:opacity-50"
            :disabled="checkingUpdate"
            @click="checkForUpdate"
          >
            <RefreshCw :size="12" :class="checkingUpdate ? 'animate-spin' : ''" />
            <span>{{ checkingUpdate ? '检查中…' : '立即检查' }}</span>
          </button>
        </div>

        <div class="text-xs text-ink-soft dark:text-night-text-soft space-y-1">
          <div>
            当前版本：<span class="font-mono text-ink dark:text-night-text">v{{ updateInfo?.current_version ?? info?.version ?? '?' }}</span>
          </div>
          <div v-if="updateInfo?.latest_version">
            最新发布：
            <span
              class="font-mono"
              :class="updateInfo.is_outdated
                ? 'text-accent dark:text-night-accent font-medium'
                : 'text-ink dark:text-night-text'"
            >v{{ updateInfo.latest_version }}</span>
            <span v-if="!updateInfo.is_outdated" class="ml-2 text-success">已是最新版 ✓</span>
            <span v-else class="ml-2 text-accent dark:text-night-accent">有新版本可用</span>
          </div>
          <div v-if="updateInfo?.last_checked_at_ms">
            最后检查：{{ formatTimestamp(updateInfo.last_checked_at_ms) }}
          </div>
          <div v-if="updateInfo?.check_enabled === false" class="text-warning">
            自动检查已关闭（UPDATE_CHECK_ENABLED=false）；手动检查仍可用
          </div>
          <div v-if="updateInfo?.last_error" class="text-warning">
            上次检查失败：{{ updateInfo.last_error }}
          </div>
        </div>

        <div
          v-if="updateInfo?.is_outdated && updateInfo.release_notes_preview"
          class="text-xs text-ink-soft dark:text-night-text-soft p-2 rounded-lg
                 bg-paper/60 dark:bg-night-bg/60 border border-ink/5 dark:border-night-text/10"
        >
          <div class="font-medium text-ink dark:text-night-text mb-1">变更预览</div>
          <p class="line-clamp-4">{{ updateInfo.release_notes_preview }}</p>
        </div>

        <div v-if="updateInfo?.release_url" class="flex gap-3 text-xs">
          <a
            :href="updateInfo.release_url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-accent dark:text-night-accent hover:underline"
          >查看完整变更日志 →</a>
        </div>

        <Transition
          enter-active-class="transition-opacity duration-200"
          enter-from-class="opacity-0"
          leave-active-class="transition-opacity duration-200"
          leave-to-class="opacity-0"
        >
          <p
            v-if="updateCheckHint"
            class="text-xs"
            :class="updateCheckHint.includes('失败') || updateCheckHint.includes('出错')
              ? 'text-warning'
              : 'text-ink-soft dark:text-night-text-soft'"
          >
            {{ updateCheckHint }}
          </p>
        </Transition>
      </section>

      <!-- 身份与人格 -->
      <section class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                      shadow-letter border border-ink/5 dark:border-night-text/10">
        <h2 class="text-base font-medium">身份</h2>
        <div class="grid grid-cols-2 gap-3">
          <label class="block">
            <span class="text-sm">你的名字</span>
            <input
              v-model="settings.selfName"
              class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10 outline-none focus:ring-2 focus:ring-accent-soft"
            />
          </label>
          <label class="block">
            <span class="text-sm">ta 的名字</span>
            <input
              v-model="settings.friendName"
              class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10 outline-none focus:ring-2 focus:ring-accent-soft"
            />
          </label>
        </div>
        <label class="block">
          <span class="text-sm">关系</span>
          <input
            v-model="settings.relationshipDescription"
            class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                   border border-ink/10 dark:border-night-text/10 outline-none focus:ring-2 focus:ring-accent-soft"
          />
        </label>
        <p class="text-xs text-ink-soft dark:text-night-text-soft">
          这些值仅控制前端文案。要真正切换 persona 模板请改 backend/.env 的 PERSONA_TEMPLATE。
        </p>
      </section>

      <!-- 外观 -->
      <section class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                      shadow-letter border border-ink/5 dark:border-night-text/10">
        <h2 class="text-base font-medium">外观</h2>
        <div class="flex gap-2">
          <button
            v-for="t in (['light','dark','system'] as const)"
            :key="t"
            class="flex-1 py-2 rounded-lg border text-sm transition-colors"
            :class="
              settings.theme === t
                ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                : 'border-ink/10 dark:border-night-text/10 text-ink-soft dark:text-night-text-soft hover:text-ink dark:hover:text-night-text'
            "
            @click="settings.theme = t"
          >
            {{ t === 'light' ? '明色' : t === 'dark' ? '暗色' : '跟随系统' }}
          </button>
        </div>
        <label class="block">
          <span class="text-sm">字号缩放：{{ Math.round(settings.fontScale * 100) }}%</span>
          <input
            v-model.number="settings.fontScale"
            type="range"
            min="0.9"
            max="1.3"
            step="0.05"
            class="mt-1 w-full"
          />
        </label>
      </section>

      <!-- 记忆 -->
      <section v-if="memory.stats" class="space-y-3 rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft
                                          shadow-letter border border-ink/5 dark:border-night-text/10">
        <h2 class="text-base font-medium">记忆</h2>
        <div class="grid grid-cols-3 gap-3 text-center">
          <div>
            <div class="text-2xl font-medium">{{ memory.stats.friend_messages }}</div>
            <div class="text-xs text-ink-soft dark:text-night-text-soft">朋友的话</div>
          </div>
          <div>
            <div class="text-2xl font-medium">{{ memory.stats.dialogue_windows }}</div>
            <div class="text-xs text-ink-soft dark:text-night-text-soft">对话片段</div>
          </div>
          <div>
            <div class="text-2xl font-medium">{{ memory.stats.live_messages }}</div>
            <div class="text-xs text-ink-soft dark:text-night-text-soft">新对话回写</div>
          </div>
        </div>
        <button
          :disabled="busy"
          class="w-full py-2 rounded-lg border border-ink/10 dark:border-night-text/10 text-sm
                 hover:bg-paper dark:hover:bg-night-bg disabled:opacity-50"
          @click="togglePause"
        >
          {{ memory.stats.writeback_paused ? '恢复回写' : '暂停回写（不再把新对话写入记忆）' }}
        </button>
      </section>
      <!-- 关系分析 -->
      <section
        class="rounded-lg bg-paper-soft dark:bg-night-bg-soft shadow-letter
               border border-ink/5 dark:border-night-text/10 divide-y divide-ink/10
               dark:divide-night-text/10"
      >
        <div class="p-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="font-medium">关系分析</h2>
              <p class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">
                使用已上传的聊天记录生成时间线和报告
              </p>
              <p class="mt-2 max-w-md text-xs leading-5 text-warning">
                所选记录中的清洗后文本可能全部发送给你配置的 Analysis API；请先确认你有权处理双方的私人记录。
              </p>
            </div>
            <button
              v-if="!analysisRunning"
              :disabled="analysisStarting"
              class="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-ink text-paper text-sm
                     dark:bg-night-accent dark:text-night-bg disabled:opacity-50"
              @click="startRelationshipAnalysis"
            >
              <RefreshCw v-if="analysisStarting" :size="15" class="animate-spin" />
              <Play v-else :size="15" />
              开始分析
            </button>
            <button
              v-else
              class="p-2 rounded-lg border border-ink/15 dark:border-night-text/15 text-ink-soft
                     dark:text-night-text-soft hover:text-warning"
              title="取消分析"
              aria-label="取消分析"
              @click="cancelRelationshipAnalysis"
            >
              <Square :size="15" />
            </button>
          </div>
          <div v-if="analysisTask" class="mt-4">
            <div class="flex items-center justify-between gap-3 font-sans text-xs text-ink-soft dark:text-night-text-soft">
              <span class="truncate">{{ analysisTask.stage }}</span>
              <span class="tabular-nums shrink-0">{{ Math.round(analysisTask.progress * 100) }}%</span>
            </div>
            <div class="mt-2 h-1 overflow-hidden rounded-sm bg-ink/10 dark:bg-night-text/10">
              <div
                class="h-full bg-accent dark:bg-night-accent transition-[width] duration-300"
                :style="{ width: `${Math.round(analysisTask.progress * 100)}%` }"
              />
            </div>
            <p v-if="analysisTask.error" class="mt-2 text-xs text-warning">{{ analysisTask.error }}</p>
          </div>
          <p v-if="analysisError" class="mt-3 text-xs text-warning">{{ analysisError }}</p>
        </div>
        <button
          class="w-full flex items-center justify-between p-4 text-left"
          @click="router.push('/timeline')"
        >
          <span class="flex items-center gap-3">
            <Route :size="18" />
            <span>
              <span class="block font-medium">关系时间线</span>
              <span class="block mt-0.5 text-xs text-ink-soft dark:text-night-text-soft">
                按阶段查看重要节点与原文证据
              </span>
            </span>
          </span>
          <ChevronRight :size="17" class="text-ink-soft dark:text-night-text-soft" />
        </button>
        <button
          class="w-full flex items-center justify-between p-4 text-left"
          @click="router.push('/report')"
        >
          <span class="flex items-center gap-3">
            <BookOpenText :size="18" />
            <span>
              <span class="block font-medium">关系与性格报告</span>
              <span class="block mt-0.5 text-xs text-ink-soft dark:text-night-text-soft">
                查看沟通模式、相处方式和证据
              </span>
            </span>
          </span>
          <ChevronRight :size="17" class="text-ink-soft dark:text-night-text-soft" />
        </button>
      </section>
      <!-- 表情包入口 -->
      <section
        class="rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft shadow-letter
               border border-ink/5 dark:border-night-text/10"
      >
        <button
          class="w-full flex items-center justify-between"
          @click="router.push('/stickers')"
        >
          <span class="flex items-center gap-2">
            <StickerIcon :size="18" />
            <span class="font-medium">表情包管理</span>
          </span>
          <ChevronRight :size="17" class="text-ink-soft dark:text-night-text-soft" />
        </button>
      </section>

      <!-- 诊断 -->
      <DiagnosticsPanel />
    </div>
  </div>
</template>
