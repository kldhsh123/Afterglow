<script setup lang="ts">
// Afterglow 配置向导（后端自带，不依赖 frontend 项目）
// 向导步骤：访问令牌 → 身份 → 关系 → 聊天 AI → 向量服务（含打标推荐）→ 导入聊天记录 → 设置密码
import { computed, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import {
  ArrowLeft, ArrowRight, CheckCircle2, AlertCircle, Loader2,
  KeyRound, Copy, RefreshCw, ExternalLink, Upload, FileText, X,
  Wand2, UserCircle2, Settings2,
} from 'lucide-vue-next'
import {
  api, getToken, setToken,
  type Preset, type TestResult, type ImportTaskState, type UploadedFile,
  type IdentityCandidate, type SetupStatus,
} from './api'
import { useLocale } from './useLocale'
import { applyEnglishDefaults } from './englishDefaults'
import { useConfigMessages } from './useConfigMessages'

const { isChinese, t, locale, setLocale } = useLocale()
const { translateBackendText } = useConfigMessages()

const step = ref(0)
const totalSteps = 8
const setupStatus = ref<SetupStatus | null>(null)
const configuredFields = ref(new Set<string>())
const existingValuesLoaded = ref(false)
const configuredServiceFields = new Set<string>()
const inheritedServiceValues: Record<string, string> = {}

// ---- 访问令牌 ----
const tokenInput = ref(getToken())
const tokenError = ref('')
const tokenChecking = ref(false)

// ---- 表单 ----
const form = reactive({
  SELF_NAME: '',
  SELF_UID: '',
  FRIEND_NAME: '',
  FRIEND_UID: '',
  RELATIONSHIP_TYPE: 'friend',
  RELATIONSHIP_DESCRIPTION: '',
  OPENAI_BASE_URL: '',
  OPENAI_API_KEY: '',
  CHAT_MODEL: '',
  CHAT_API_PROTOCOL: 'chat_completions' as 'chat_completions' | 'responses',
  EMBEDDING_API_URL: '',
  EMBEDDING_API_KEY: '',
  EMBEDDING_MODEL: '',
  EMBEDDING_DIM: 1024,
  EMBEDDING_INPUT_MODE: 'array' as 'array' | 'single',
  LABELING_ENABLED: true,
  LABEL_API_URL: '',
  LABEL_API_KEY: '',
  LABEL_MODEL: '',
  // 打标并发：智谱 GLM-4-Flash 免费账号上限 20，默认 19 留 1 余量
  // 其它中转站按各自上限调整
  LABEL_MAX_CONCURRENCY: 19,
  // 生活时间线（必备：backend 总会构造 life client；选择复用哪个模型）
  // 默认推荐复用打标模型 —— 复用主聊天模型会因为大模型推理慢拖慢整体响应。
  LIFE_REUSE_LABEL: true,
  LIFE_API_URL: '',
  LIFE_API_KEY: '',
  LIFE_MODEL: '',
  // 互动决策小模型（可选）：规则层判完后用小模型微调意图。同样推荐复用打标模型。
  RESPONSE_POLICY_MODEL_ENABLED: false,
  RESPONSE_POLICY_REUSE_LABEL: true,
  RESPONSE_POLICY_API_URL: '',
  RESPONSE_POLICY_API_KEY: '',
  RESPONSE_POLICY_MODEL: '',
  // 视觉理解（可选）
  VISION_ENABLED: false,
  CHAT_MODEL_SUPPORTS_VISION: false,
  VISION_API_URL: '',
  VISION_API_KEY: '',
  VISION_MODEL: '',
  // 联网搜索（可选）
  WEB_ACCESS_ENABLED: false,
  WEB_SEARCH_PROVIDER: 'tavily' as 'tavily' | 'searxng',
  WEB_SEARCH_BASE_URL: 'https://api.tavily.com',
  WEB_SEARCH_API_KEY: '',
  WEB_FETCH_ENABLED: true,
  // 检索增强（可选）：query 改写 + cross-encoder 粗排
  // LLM 精排（RERANK_*）已从向导移除——实测对 glm-4-flash 这类弱小模型是负优化
  // （单次 15-20s，区分度反不如 cross-encoder）。高级用户仍可在 .env 手动开启。
  QUERY_REWRITE_ENABLED: false,
  // Cross-encoder 粗排（与 RERANK_* 互补，不是替换；两个都开 = 两阶段质量最高）
  CROSS_RERANK_ENABLED: false,
  CROSS_RERANK_PROTOCOL: 'jina' as 'jina' | 'dashscope',
  CROSS_RERANK_API_URL: '',
  CROSS_RERANK_API_KEY: '',
  CROSS_RERANK_MODEL: '',
  // 切分策略（影响"下一次导入"如何把聊天记录切成 chunks；对已入库 chunks 无影响）
  CHUNKING_STRATEGY: 'fixed' as 'fixed' | 'adaptive',
  ADAPTIVE_CHUNK_MODEL_ENABLED: false,
  // 复用打标 / 复用 rerank 模型：留空 ADAPTIVE_CHUNK_API_* 让后端 fallback
  ADAPTIVE_CHUNK_REUSE_LABEL: true,
  ADAPTIVE_CHUNK_API_URL: '',
  ADAPTIVE_CHUNK_API_KEY: '',
  ADAPTIVE_CHUNK_MODEL: '',
  // 0 = 跟随 fallback（复用打标时 = LABEL_MAX_CONCURRENCY，否则 = 4）；>0 强制覆盖
  ADAPTIVE_CHUNK_MAX_CONCURRENCY: 0,
  XUWEN_API_KEY: '',
})

const chatPresets = shallowRef<Preset[]>([])
const embPresets = shallowRef<Preset[]>([])
const labelPresets = shallowRef<Preset[]>([])
const rerankerPresets = shallowRef<Preset[]>([])
const crossRerankerPresets = shallowRef<Preset[]>([])

// Load saved values first; changing UI language must not replace an existing provider.
watch(locale, () => {
  if (!isChinese.value && existingValuesLoaded.value) {
    applyEnglishDefaults(form, configuredServiceFields, chatPresets.value.find(p => p.id === 'gemini'), inheritedServiceValues)
  }
})

const chatTest = ref<TestResult | null>(null)
const chatTesting = ref(false)
const embTest = ref<TestResult | null>(null)
const embTesting = ref(false)
const labelTest = ref<TestResult | null>(null)
const labelTesting = ref(false)
// 允许跳过可用性校验：网络波动 / 代理问题导致连通测试失败时，
// 让用户在确认配置无误后强制继续。仅放行"下一步"，不绕过必填字段校验。
// 按【服务】拆分而非按【步骤】，避免 embedding 跳过顺带放行 label 校验。
// 任一关键字段被修改 → 对应 skip + test 状态自动清空，避免残留旧态。
const skipChatCheck = ref(false)
const skipEmbeddingCheck = ref(false)
const skipLabelCheck = ref(false)

// 字段变化 → 清空对应的 test 结果与 skip 标志，强迫用户重测/重勾
// （防止"先填错→失败→跳过→改字段"留下错误的放行状态）。
watch(
  () => [
    form.OPENAI_BASE_URL,
    form.OPENAI_API_KEY,
    form.CHAT_MODEL,
    form.CHAT_API_PROTOCOL,
  ],
  () => {
    chatTest.value = null
    skipChatCheck.value = false
  },
)
watch(
  () => [form.EMBEDDING_API_URL, form.EMBEDDING_API_KEY, form.EMBEDDING_MODEL, form.EMBEDDING_DIM],
  () => {
    embTest.value = null
    skipEmbeddingCheck.value = false
  },
)
watch(
  () => [form.LABEL_API_URL, form.LABEL_API_KEY, form.LABEL_MODEL, form.LABELING_ENABLED],
  () => {
    labelTest.value = null
    skipLabelCheck.value = false
  },
)

const uploadedFiles = ref<UploadedFile[]>([])
const importTask = ref<ImportTaskState | null>(null)
const importStarting = ref(false)
// 重启后端 / 后端任务被清空但前端还有 uploadedFiles 时，提示用户"可以续传"
const resumeHint = ref('')
let importSse: EventSource | null = null

// localStorage 持久化导入任务，关掉页面也能回来追进度
const IMPORT_TASK_KEY = 'afterglow.import.activeTaskId'
const IMPORT_FILES_KEY = 'afterglow.import.uploadedFiles'

function persistImportTask(id: string | null) {
  if (typeof localStorage === 'undefined') return
  if (id) localStorage.setItem(IMPORT_TASK_KEY, id)
  else localStorage.removeItem(IMPORT_TASK_KEY)
}
function persistUploadedFiles(files: UploadedFile[]) {
  if (typeof localStorage === 'undefined') return
  if (files.length) localStorage.setItem(IMPORT_FILES_KEY, JSON.stringify(files))
  else localStorage.removeItem(IMPORT_FILES_KEY)
}
function loadPersistedFiles(): UploadedFile[] {
  if (typeof localStorage === 'undefined') return []
  try {
    const raw = localStorage.getItem(IMPORT_FILES_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

// 第 1 步：从聊天文件识别 UID
const inspecting = ref(false)
const inspectError = ref('')
const inspectCandidates = ref<IdentityCandidate[]>([])
const inspectFormat = ref<string>('')

const saving = ref(false)
const saveError = ref('')
const finished = ref(false)
const backupPath = ref<string | null>(null)

const relationships = computed(() => [
  { value: 'friend', label: t('friend') },
  { value: 'lover', label: t('partner') },
  { value: 'family', label: t('family') },
  { value: 'colleague', label: t('colleague') },
  { value: 'custom', label: t('other') },
])

const progress = computed(() => Math.round(((step.value) / (totalSteps)) * 100))

function hasConfigured(key: string): boolean {
  return configuredFields.value.has(key)
}

function hasValueOrConfigured(key: keyof typeof form): boolean {
  const value = form[key]
  return (typeof value === 'string' && value.trim().length > 0) || hasConfigured(key)
}

const stepItems = computed(() => [
  { id: 1, label: t('identity'), complete: setupStatus.value?.identity_ok ?? false, optional: false },
  { id: 2, label: t('relationship'), complete: hasConfigured('RELATIONSHIP_TYPE'), optional: false },
  { id: 3, label: t('chatAI'), complete: setupStatus.value?.chat_ok ?? false, optional: false },
  { id: 4, label: t('embeddingsLabeling'), complete: setupStatus.value?.embedding_ok ?? false, optional: false },
  { id: 5, label: t('optionalFeatures'), complete: true, optional: true },
  { id: 6, label: t('retrievalEnhancements'), complete: true, optional: true },
  { id: 7, label: t('importHistory'), complete: importTask.value?.status === 'done', optional: true },
  { id: 8, label: t('accessPassword'), complete: setupStatus.value?.auth_ok ?? false, optional: false },
])

function firstIncompleteStep(status: SetupStatus): number {
  if (!status.identity_ok) return 1
  if (!status.chat_ok) return 3
  if (!status.embedding_ok) return 4
  if (!status.auth_ok) return 8
  return -1
}

async function goToStep(target: number) {
  if (step.value > 0 && step.value !== target) {
    await saveConfig({ silent: true })
  }
  finished.value = false
  step.value = target
}

// ---- 访问令牌校验 ----
async function checkToken() {
  if (!tokenInput.value.trim()) {
    tokenError.value = t('pasteTheSetupTokenPrintedInThe')
    return
  }
  tokenChecking.value = true
  tokenError.value = ''
  try {
    setToken(tokenInput.value)
  } catch (e) {
    tokenError.value = (e as Error).message
    tokenChecking.value = false
    return
  }
  try {
    await api.ping()
    const [presetsData, status, valuesLoaded] = await Promise.all([
      api.presets(),
      api.status(),
      loadExistingValues(),
    ])
    setupStatus.value = status
    chatPresets.value = presetsData.chat
    embPresets.value = presetsData.embedding
    labelPresets.value = presetsData.label
    rerankerPresets.value = presetsData.reranker
    crossRerankerPresets.value = presetsData.cross_reranker
    existingValuesLoaded.value = valuesLoaded
    if (!isChinese.value && existingValuesLoaded.value) {
      applyEnglishDefaults(form, configuredServiceFields, chatPresets.value.find(p => p.id === 'gemini'), inheritedServiceValues)
    }
    step.value = firstIncompleteStep(status)
    // 访问令牌校验成功后才尝试恢复未完成的导入任务，
    // 否则用旧令牌调用 /import/* 会返回 401，且用户没机会更新令牌。
    await tryResumeUnfinishedImport()
  } catch (e) {
    tokenError.value = (e as Error).message || t('validationFailed')
  } finally {
    tokenChecking.value = false
  }
}

async function tryResumeUnfinishedImport() {
  if (typeof localStorage === 'undefined') return
  const storedId = localStorage.getItem(IMPORT_TASK_KEY)
  if (!storedId) return
  try {
    const t = await api.getTask(storedId)
    importTask.value = t
    if (['done', 'failed', 'cancelled'].includes(t.status)) {
      persistImportTask(null)
      if (!hasValueOrConfigured('XUWEN_API_KEY')) step.value = 8
    } else {
      step.value = 7
      subscribeTask(storedId)
    }
  } catch {
    // 任务在后端找不到（最常见原因：后端重启）。底层 chunk_id 去重支持续传。
    persistImportTask(null)
    if (uploadedFiles.value.length) {
      step.value = 7
      resumeHint.value = [
        t('anUnfinishedImportWasFoundTheBackend'),
        t('processedRecordsArePreservedInTheVector'),
        t('clickStartImportToSkipStoredRecords'),
      ].join(' ')
    }
  }
}

async function loadExistingValues() {
  // 已写入的 .env 字段反向填回表单，让用户重开页面能接着填
  // 密钥字段后端只返回掩码，这里跳过（表单字段保持空，用户重新填）
  const BOOL_FIELDS = new Set([
    'LABELING_ENABLED',
    'VISION_ENABLED',
    'CHAT_MODEL_SUPPORTS_VISION',
    'WEB_ACCESS_ENABLED',
    'WEB_FETCH_ENABLED',
    'RESPONSE_POLICY_MODEL_ENABLED',
    'QUERY_REWRITE_ENABLED',
    'CROSS_RERANK_ENABLED',
    'ADAPTIVE_CHUNK_MODEL_ENABLED',
  ])
  try {
    const data = await api.values()
    configuredServiceFields.clear()
    for (const key of Object.keys(inheritedServiceValues)) delete inheritedServiceValues[key]
    for (const [k, v] of Object.entries(data.values)) {
      if (v.set && v.configured !== false) configuredServiceFields.add(k)
      if (v.configured === false && v.value !== undefined) inheritedServiceValues[k] = v.value
      if (v.set) configuredFields.value.add(k)
      if (!v.set || v.value === undefined || v.value === null) continue
      if (!(k in form)) continue
      if (k === 'EMBEDDING_DIM' || k === 'LABEL_MAX_CONCURRENCY' || k === 'ADAPTIVE_CHUNK_MAX_CONCURRENCY') {
        ;(form as any)[k] = parseInt(v.value, 10) || (form as any)[k]
      } else if (BOOL_FIELDS.has(k)) {
        ;(form as any)[k] = v.value === 'true' || v.value === '1'
      } else {
        ;(form as any)[k] = v.value
      }
    }
    return true
  } catch { return false /* 首次运行时可能还没有 .env */ }
}

async function next() {
  if (step.value >= totalSteps) return
  // 静默保存当前状态到 .env：用户随时关网页都不会丢已填字段。
  // saveConfig 内部会跳过空字符串字段，避免覆盖之前已填好的字段。
  // 第 0 步（访问令牌校验）不写 .env，跳过
  if (step.value > 0 && !await saveConfig({ silent: true })) return
  step.value += 1
}
function prev() { if (step.value > 0) step.value -= 1 }

// ---- 服务预设 ----
function applyChatPreset(p: Preset) {
  // custom 预设也带示例 base_url，让用户看到 URL 格式后替换
  // 如果用户已经填的是某个真实中转站（不在任何已知 preset 里），点 custom 不覆盖
  if (p.id === 'custom') {
    const isKnown = chatPresets.value.some(
      x => x.id !== 'custom' && x.base_url === form.OPENAI_BASE_URL,
    )
    if (!isKnown && form.OPENAI_BASE_URL) {
      // 保持用户已填的真实地址
      chatTest.value = null
      return
    }
  }
  // 当前内置服务商预设只承诺兼容 Chat Completions。
  form.CHAT_API_PROTOCOL = 'chat_completions'
  form.OPENAI_BASE_URL = p.base_url
  form.CHAT_MODEL = p.default_model
  chatTest.value = null
}
function applyEmbPreset(p: Preset) {
  if (p.id === 'custom') {
    const isKnown = embPresets.value.some(
      x => x.id !== 'custom' && x.base_url === form.EMBEDDING_API_URL,
    )
    if (!isKnown && form.EMBEDDING_API_URL) {
      embTest.value = null
      return
    }
  }
  form.EMBEDDING_API_URL = p.base_url
  form.EMBEDDING_MODEL = p.default_model
  // 根据默认模型设置默认维度
  if (p.default_model.includes('Qwen3-Embedding-8B')) form.EMBEDDING_DIM = 4096
  else if (p.default_model === 'xop3qwen8bembedding') form.EMBEDDING_DIM = 4096
  else if (p.default_model === 'text-embedding-v4') form.EMBEDDING_DIM = 3072
  else if (p.default_model.includes('text-embedding-3-large')) form.EMBEDDING_DIM = 3072
  else if (p.default_model.includes('nomic-embed-text')) form.EMBEDDING_DIM = 768
  else if (p.default_model.startsWith('text-embedding-v')) form.EMBEDDING_DIM = 1024
  embTest.value = null
}
function applyLabelPreset(p: Preset) {
  if (p.id === 'custom') {
    const isKnown = labelPresets.value.some(
      x => x.id !== 'custom' && x.base_url === form.LABEL_API_URL,
    )
    if (!isKnown && form.LABEL_API_URL) {
      labelTest.value = null
      return
    }
  }
  form.LABEL_API_URL = p.base_url
  form.LABEL_MODEL = p.default_model
  labelTest.value = null
}
function applyCrossRerankerPreset(p: Preset) {
  form.CROSS_RERANK_API_URL = p.base_url
  form.CROSS_RERANK_MODEL = p.default_model
  // protocol 从 preset.extra 取（dashscope-gte 是 dashscope，其它都是 jina）
  const proto = p.extra?.protocol === 'dashscope' ? 'dashscope' : 'jina'
  form.CROSS_RERANK_PROTOCOL = proto
}
function applyAdaptiveChunkPreset(p: Preset) {
  // "复用打标" 预设：清空独立 ADAPTIVE_CHUNK_API_*，后端按 LABEL→LIFE→主 LLM 顺序 fallback
  if (p.id === 'reuse-label') {
    form.ADAPTIVE_CHUNK_REUSE_LABEL = true
    form.ADAPTIVE_CHUNK_API_URL = ''
    form.ADAPTIVE_CHUNK_API_KEY = ''
    form.ADAPTIVE_CHUNK_MODEL = ''
    return
  }
  form.ADAPTIVE_CHUNK_REUSE_LABEL = false
  form.ADAPTIVE_CHUNK_API_URL = p.base_url
  form.ADAPTIVE_CHUNK_MODEL = p.default_model
}

// 切分策略只能在"启动导入前 / 上一轮导入已经结束"时修改。
// importer 启动时已经读完 Settings.CHUNKING_STRATEGY/ADAPTIVE_CHUNK_*；
// 在跑期间改设置不会影响当前任务，反而误导用户以为生效，所以整体锁定。
const chunkingStrategyLocked = computed<boolean>(() => {
  const st = importTask.value?.status
  if (!st) return false
  return ['pending', 'parsing', 'importing', 'labeling', 'persona'].includes(st)
})

// ---- 连通性测试 ----
async function testChat() {
  chatTesting.value = true; chatTest.value = null
  try {
    chatTest.value = await api.testChat(
      form.OPENAI_BASE_URL,
      form.OPENAI_API_KEY,
      form.CHAT_MODEL,
      form.CHAT_API_PROTOCOL,
    )
  } catch (e) {
    chatTest.value = { ok: false, message: (e as Error).message }
  } finally { chatTesting.value = false }
}
async function testEmbedding() {
  embTesting.value = true; embTest.value = null
  try {
    embTest.value = await api.testEmbedding(
      form.EMBEDDING_API_URL, form.EMBEDDING_API_KEY, form.EMBEDDING_MODEL,
      { input_mode: form.EMBEDDING_INPUT_MODE, dim: form.EMBEDDING_DIM },
    )
  } catch (e) {
    embTest.value = { ok: false, message: (e as Error).message }
  } finally { embTesting.value = false }
}
function acceptActualDim() {
  const actual = embTest.value?.extra?.actual_dim
  if (typeof actual === 'number') {
    form.EMBEDDING_DIM = actual
    embTest.value = null
  }
}
async function testLabel() {
  labelTesting.value = true; labelTest.value = null
  try {
    labelTest.value = await api.testChat(form.LABEL_API_URL, form.LABEL_API_KEY, form.LABEL_MODEL)
  } catch (e) {
    labelTest.value = { ok: false, message: (e as Error).message }
  } finally { labelTesting.value = false }
}

// ---- API 密钥 ----
async function genApiKey() {
  const data = await api.generateApiKey()
  form.XUWEN_API_KEY = data.token
}
function copyApiKey() {
  if (form.XUWEN_API_KEY && navigator.clipboard) {
    navigator.clipboard.writeText(form.XUWEN_API_KEY)
  }
}

// ---- 第 1 步：选文件 → 上传 + 嗅探 + 合并候选 ----
// 多文件场景：同时上传多份导出（QQ + 微信 / 多账号），后端逐个嗅探，
// 前端合并候选按 UID 去重；第 7 步直接复用这些已上传文件，不再要求重传。
function mergeCandidates(prev: IdentityCandidate[], next: IdentityCandidate[]): IdentityCandidate[] {
  const rank = { self: 0, friend: 1, unknown: 2 } as const
  const map = new Map<string, IdentityCandidate>(prev.map(c => [c.uid, c]))
  for (const c of next) {
    const exist = map.get(c.uid)
    if (!exist) {
      map.set(c.uid, c)
    } else if (rank[c.role_hint] < rank[exist.role_hint]) {
      // 取 role_hint 更强的（self > friend > unknown）
      map.set(c.uid, { ...exist, role_hint: c.role_hint })
    }
  }
  return Array.from(map.values())
}

async function onIdentifyFilesPicked(ev: Event) {
  const target = ev.target as HTMLInputElement
  const files = Array.from(target.files || [])
  if (!files.length) return
  inspecting.value = true
  inspectError.value = ''
  try {
    const res = await api.uploadFiles(files)
    // 累加到已上传列表（第 7 步会直接使用）
    uploadedFiles.value.push(...res.uploaded)

    // 合并候选 + 更新格式
    let allCandidates: IdentityCandidate[] = [...inspectCandidates.value]
    const fileErrors: string[] = []
    for (const f of res.uploaded) {
      if (f.error) fileErrors.push(`${f.name}：${f.error}`)
      if (f.candidates && f.candidates.length) {
        allCandidates = mergeCandidates(allCandidates, f.candidates)
      }
      if (f.format && f.format !== 'unknown' && !inspectFormat.value) {
        inspectFormat.value = f.format
      }
    }
    inspectCandidates.value = allCandidates
    if (fileErrors.length) inspectError.value = fileErrors.join('；')

    // 自动应用 role_hint=self/friend 到尚未填写的字段
    const self = allCandidates.find(c => c.role_hint === 'self')
    const friend = allCandidates.find(c => c.role_hint === 'friend')
    if (self && !form.SELF_UID) { form.SELF_NAME = self.name; form.SELF_UID = self.uid }
    if (friend && !form.FRIEND_UID) { form.FRIEND_NAME = friend.name; form.FRIEND_UID = friend.uid }

    persistUploadedFiles(uploadedFiles.value)
  } catch (e) {
    inspectError.value = (e as Error).message
  } finally {
    inspecting.value = false
    target.value = ''
  }
}

// "设为我 / 设为朋友"：toggle 行为 + self/friend 互斥
// - 已是该角色 → 再点一次取消
// - 不是该角色 → 加入该角色，同时从对面角色列表里移除（避免同时是 self 和 friend）
function _splitUids(field: 'SELF_UID' | 'FRIEND_UID'): string[] {
  const cur = (form as any)[field] as string
  return cur ? cur.split(',').map((s: string) => s.trim()).filter(Boolean) : []
}
function _joinUids(field: 'SELF_UID' | 'FRIEND_UID', list: string[]) {
  ;(form as any)[field] = list.join(',')
}
function _toggleRole(c: IdentityCandidate, target: 'self' | 'friend') {
  const myField = target === 'self' ? 'SELF_UID' : 'FRIEND_UID'
  const otherField = target === 'self' ? 'FRIEND_UID' : 'SELF_UID'
  const myNameField = target === 'self' ? 'SELF_NAME' : 'FRIEND_NAME'

  const mine = _splitUids(myField)
  const idx = mine.indexOf(c.uid)
  if (idx >= 0) {
    // 已是该角色 → 移除（取消）
    mine.splice(idx, 1)
    _joinUids(myField, mine)
    // 名字若正好等于当前候选名，清空（用户可能想换）
    if ((form as any)[myNameField] === c.name && mine.length === 0) {
      ;(form as any)[myNameField] = ''
    }
    return
  }
  // 加入该角色，先把对面同 uid 移掉（避免同时 self+friend）
  const other = _splitUids(otherField)
  const oi = other.indexOf(c.uid)
  if (oi >= 0) {
    other.splice(oi, 1)
    _joinUids(otherField, other)
  }
  mine.push(c.uid)
  _joinUids(myField, mine)
  if (!(form as any)[myNameField]) (form as any)[myNameField] = c.name
}
function pickAsSelf(c: IdentityCandidate) { _toggleRole(c, 'self') }
function pickAsFriend(c: IdentityCandidate) { _toggleRole(c, 'friend') }

function isPickedAsSelf(c: IdentityCandidate): boolean {
  return _splitUids('SELF_UID').includes(c.uid)
}
function isPickedAsFriend(c: IdentityCandidate): boolean {
  return _splitUids('FRIEND_UID').includes(c.uid)
}

// 未被分配到 self/friend 任何一边的候选 UID。导入时这些 UID 会被当陌生人忽略，
// 跨平台账号场景下用户容易漏选（QQ + 微信 4 个候选只点了 2 个），需要明显提示。
const unassignedCandidates = computed<IdentityCandidate[]>(() => {
  return inspectCandidates.value.filter(
    c => !isPickedAsSelf(c) && !isPickedAsFriend(c),
  )
})

// ---- 导入流程 ----
function removeUploaded(idx: number) {
  uploadedFiles.value.splice(idx, 1)
  persistUploadedFiles(uploadedFiles.value)
}

async function startImport() {
  // 防御性锁：防止用户疯狂点击或 saveConfig await 期间被重复触发。
  // 只有"进行中"的任务才阻止重启；failed/cancelled 状态允许重新启动。
  if (importStarting.value) return
  if (importTask.value && !['done', 'failed', 'cancelled'].includes(importTask.value.status)) return
  if (!uploadedFiles.value.length) return
  importStarting.value = true
  saveError.value = ''
  // 清掉旧的终态任务，避免 UI 同时显示新旧两个状态
  if (importTask.value && ['done', 'failed', 'cancelled'].includes(importTask.value.status)) {
    importTask.value = null
    persistImportTask(null)
  }
  try {
    const ok = await saveConfig({ silent: true })
    if (!ok) return
    const files = uploadedFiles.value.map(f => f.saved_as)
    const names = uploadedFiles.value.map(f => f.name)
    const res = await api.startImport(files, names)
    persistImportTask(res.task_id)
    resumeHint.value = ''
    subscribeTask(res.task_id)
  } catch (e) {
    const msg = (e as Error).message || ''
    saveError.value = t('importStartFailed', { error: msg })
    // 已上传的文件路径可能因为后端重启 / 清理而失效，提示用户重新上传
    if (msg.includes('文件不存在') || msg.includes('not exist') || msg.includes('404')) {
      uploadedFiles.value = []
      persistUploadedFiles([])
      saveError.value = t('uploadedFilesAreMissingFromTheBackend')
    }
  } finally {
    importStarting.value = false
  }
}

function subscribeTask(taskId: string) {
  if (importSse) importSse.close()
  const es = new EventSource(api.taskStreamUrl(taskId))
  let consecutiveErrors = 0
  es.onopen = () => {
    consecutiveErrors = 0
    if (saveError.value.startsWith(t('importProgressConnectionInterrupted'))) {
      saveError.value = ''
    }
  }
  es.onmessage = (ev) => {
    try {
      importTask.value = JSON.parse(ev.data)
      if (importTask.value && ['done', 'failed', 'cancelled'].includes(importTask.value.status)) {
        es.close()
        // 任务结束清掉 localStorage 任务标记，但保留 uploadedFiles 列表
        persistImportTask(null)
      }
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e)
      saveError.value = t('invalidImportProgress', { error: detail })
      es.close()
    }
  }
  es.onerror = () => {
    consecutiveErrors += 1
    if (es.readyState === EventSource.CLOSED) {
      saveError.value = t('theProgressConnectionIsClosedRefreshThe')
      importSse = null
      return
    }
    if (consecutiveErrors >= 2) {
      saveError.value = t('theProgressConnectionWasInterruptedYourBrowser')
    }
    // 不主动关闭：EventSource 会按浏览器原生策略自动重连。
  }
  importSse = es
}

async function cancelImport() {
  if (importTask.value) await api.cancelTask(importTask.value.task_id)
}

// ---- 保存配置 ----
async function saveConfig(opts: { silent?: boolean } = {}): Promise<boolean> {
  saving.value = true; saveError.value = ''
  try {
    // 仅把"非空"字段写入 .env。空字符串会被跳过 →
    // 避免在前几步保存时把后几步还没填的字段误写成空，覆盖 .env 已有值。
    // bool / 开关字段属于"已表态"，永远写入。
    const values: Record<string, string> = {}
    const putStr = (k: string, v: string) => { if (v && v.trim()) values[k] = v }
    const putBool = (k: string, v: boolean) => { values[k] = v ? 'true' : 'false' }

    putStr('SELF_NAME', form.SELF_NAME)
    putStr('SELF_UID', form.SELF_UID)
    putStr('FRIEND_NAME', form.FRIEND_NAME)
    putStr('FRIEND_UID', form.FRIEND_UID)
    putStr('RELATIONSHIP_TYPE', form.RELATIONSHIP_TYPE)
    if (form.RELATIONSHIP_TYPE === 'custom') {
      putStr('RELATIONSHIP_DESCRIPTION', form.RELATIONSHIP_DESCRIPTION)
    } else {
      // 切回非 custom 时显式清空，否则后端 resolved_relationship_description 仍会取旧描述
      values.RELATIONSHIP_DESCRIPTION = ''
    }
    putStr('OPENAI_BASE_URL', form.OPENAI_BASE_URL)
    putStr('OPENAI_API_KEY', form.OPENAI_API_KEY)
    putStr('CHAT_MODEL', form.CHAT_MODEL)
    putStr('CHAT_API_PROTOCOL', form.CHAT_API_PROTOCOL)
    putStr('EMBEDDING_API_URL', form.EMBEDDING_API_URL)
    putStr('EMBEDDING_API_KEY', form.EMBEDDING_API_KEY)
    putStr('EMBEDDING_MODEL', form.EMBEDDING_MODEL)
    if (form.EMBEDDING_DIM > 0) values.EMBEDDING_DIM = String(form.EMBEDDING_DIM)
    putStr('EMBEDDING_INPUT_MODE', form.EMBEDDING_INPUT_MODE)
    putBool('LABELING_ENABLED', form.LABELING_ENABLED)
    if (form.LABELING_ENABLED) {
      putStr('LABEL_API_URL', form.LABEL_API_URL)
      putStr('LABEL_API_KEY', form.LABEL_API_KEY)
      putStr('LABEL_MODEL', form.LABEL_MODEL)
      if (form.LABEL_MAX_CONCURRENCY > 0) {
        values.LABEL_MAX_CONCURRENCY = String(form.LABEL_MAX_CONCURRENCY)
      }
    }
    // 生活时间线：复用打标 → 把 LABEL_* 显式复制到 LIFE_*
    if (form.LIFE_REUSE_LABEL && form.LABELING_ENABLED && form.LABEL_API_URL) {
      putStr('LIFE_API_URL', form.LABEL_API_URL)
      putStr('LIFE_API_KEY', form.LABEL_API_KEY)
      putStr('LIFE_MODEL', form.LABEL_MODEL)
    } else {
      putStr('LIFE_API_URL', form.LIFE_API_URL)
      putStr('LIFE_API_KEY', form.LIFE_API_KEY)
      putStr('LIFE_MODEL', form.LIFE_MODEL)
    }
    putBool('RESPONSE_POLICY_MODEL_ENABLED', form.RESPONSE_POLICY_MODEL_ENABLED)
    if (form.RESPONSE_POLICY_MODEL_ENABLED) {
      if (form.RESPONSE_POLICY_REUSE_LABEL && form.LABELING_ENABLED && form.LABEL_API_URL) {
        putStr('RESPONSE_POLICY_API_URL', form.LABEL_API_URL)
        putStr('RESPONSE_POLICY_API_KEY', form.LABEL_API_KEY)
        putStr('RESPONSE_POLICY_MODEL', form.LABEL_MODEL)
      } else {
        putStr('RESPONSE_POLICY_API_URL', form.RESPONSE_POLICY_API_URL)
        putStr('RESPONSE_POLICY_API_KEY', form.RESPONSE_POLICY_API_KEY)
        putStr('RESPONSE_POLICY_MODEL', form.RESPONSE_POLICY_MODEL)
      }
    }
    putBool('VISION_ENABLED', form.VISION_ENABLED)
    if (form.VISION_ENABLED) {
      putBool('CHAT_MODEL_SUPPORTS_VISION', form.CHAT_MODEL_SUPPORTS_VISION)
      if (!form.CHAT_MODEL_SUPPORTS_VISION) {
        putStr('VISION_API_URL', form.VISION_API_URL)
        putStr('VISION_API_KEY', form.VISION_API_KEY)
        putStr('VISION_MODEL', form.VISION_MODEL)
      } else {
        // 主模型支持视觉时清空独立 vision 配置，避免旧值还在 .env 干扰
        values.VISION_API_URL = ''
        values.VISION_API_KEY = ''
        values.VISION_MODEL = ''
      }
    } else {
      // 整体关掉视觉 → 清空所有 vision 字段，避免下次重启误启用
      values.CHAT_MODEL_SUPPORTS_VISION = 'false'
      values.VISION_API_URL = ''
      values.VISION_API_KEY = ''
      values.VISION_MODEL = ''
    }
    putBool('WEB_ACCESS_ENABLED', form.WEB_ACCESS_ENABLED)
    if (form.WEB_ACCESS_ENABLED) {
      putStr('WEB_SEARCH_PROVIDER', form.WEB_SEARCH_PROVIDER)
      putStr('WEB_SEARCH_BASE_URL', form.WEB_SEARCH_BASE_URL)
      // 切到 SearXNG 时不需要 API key，必须显式清空旧的 Tavily key，
      // 否则 web_search 客户端会把旧 key 当 Bearer 发给 SearXNG 实例
      if (form.WEB_SEARCH_PROVIDER === 'tavily') {
        putStr('WEB_SEARCH_API_KEY', form.WEB_SEARCH_API_KEY)
      } else {
        values.WEB_SEARCH_API_KEY = ''
      }
      putBool('WEB_FETCH_ENABLED', form.WEB_FETCH_ENABLED)
    } else {
      // 整体关掉联网搜索 → 清空相关 key
      values.WEB_SEARCH_API_KEY = ''
    }
    // 检索增强（可选）
    putBool('QUERY_REWRITE_ENABLED', form.QUERY_REWRITE_ENABLED)
    putBool('CROSS_RERANK_ENABLED', form.CROSS_RERANK_ENABLED)
    if (form.CROSS_RERANK_ENABLED) {
      putStr('CROSS_RERANK_PROTOCOL', form.CROSS_RERANK_PROTOCOL)
      putStr('CROSS_RERANK_API_URL', form.CROSS_RERANK_API_URL)
      putStr('CROSS_RERANK_API_KEY', form.CROSS_RERANK_API_KEY)
      putStr('CROSS_RERANK_MODEL', form.CROSS_RERANK_MODEL)
    }
    // 切分策略
    putStr('CHUNKING_STRATEGY', form.CHUNKING_STRATEGY)
    putBool('ADAPTIVE_CHUNK_MODEL_ENABLED', form.ADAPTIVE_CHUNK_MODEL_ENABLED)
    if (form.CHUNKING_STRATEGY === 'adaptive' && form.ADAPTIVE_CHUNK_MODEL_ENABLED) {
      // 复用打标：清空独立 ADAPTIVE_CHUNK_API_* 让后端走 LABEL→LIFE→主 LLM fallback
      if (form.ADAPTIVE_CHUNK_REUSE_LABEL && form.LABELING_ENABLED && form.LABEL_API_URL) {
        values.ADAPTIVE_CHUNK_API_URL = ''
        values.ADAPTIVE_CHUNK_API_KEY = ''
        values.ADAPTIVE_CHUNK_MODEL = ''
      } else {
        putStr('ADAPTIVE_CHUNK_API_URL', form.ADAPTIVE_CHUNK_API_URL)
        putStr('ADAPTIVE_CHUNK_API_KEY', form.ADAPTIVE_CHUNK_API_KEY)
        putStr('ADAPTIVE_CHUNK_MODEL', form.ADAPTIVE_CHUNK_MODEL)
      }
      // 并发：0 = 跟随 fallback，>0 强制覆盖
      if (form.ADAPTIVE_CHUNK_MAX_CONCURRENCY >= 0) {
        values.ADAPTIVE_CHUNK_MAX_CONCURRENCY = String(form.ADAPTIVE_CHUNK_MAX_CONCURRENCY)
      }
    }
    putStr('XUWEN_API_KEY', form.XUWEN_API_KEY)

    // 没有任何字段要写 → 跳过请求（避免空 PUT 触发不必要的备份）
    if (Object.keys(values).length === 0) return true
    const result = await api.putValues(values)
    if (!result.ok) {
      const errs = result.errors?.map(e => `${e.field}: ${e.message}`).join('；') || t('validationFailed')
      throw new Error(errs)
    }
    if (result.backup) backupPath.value = result.backup
    const nextConfigured = new Set(configuredFields.value)
    for (const [key, value] of Object.entries(values)) {
      configuredServiceFields.add(key)
      if (value.trim()) nextConfigured.add(key)
      else nextConfigured.delete(key)
    }
    configuredFields.value = nextConfigured
    setupStatus.value = await api.status()
    return true
  } catch (e) {
    if (!opts.silent) saveError.value = (e as Error).message
    return false
  } finally {
    saving.value = false
  }
}

async function finishWizard() {
  const ok = await saveConfig()
  if (!ok) return
  if (importSse) importSse.close()
  finished.value = true
}

const canNext = computed(() => {
  switch (step.value) {
    case 1: return !!(form.SELF_NAME && form.SELF_UID && form.FRIEND_NAME && form.FRIEND_UID)
    case 2: return form.RELATIONSHIP_TYPE !== 'custom' || !!form.RELATIONSHIP_DESCRIPTION
    case 3: {
      // 必填字段必须填齐；连通性测试通过 OR 用户显式跳过（仅跳过可用性，不绕过必填）
      const filled = !!(
        form.OPENAI_BASE_URL
        && hasValueOrConfigured('OPENAI_API_KEY')
        && form.CHAT_MODEL
      )
      if (!filled) return false
      return setupStatus.value?.chat_ok === true || !!chatTest.value?.ok || skipChatCheck.value
    }
    case 4: {
      // 向量服务：必填三件套；连通通过 OR 跳过
      const embFilled = !!(
        form.EMBEDDING_API_URL
        && hasValueOrConfigured('EMBEDDING_API_KEY')
        && form.EMBEDDING_MODEL
      )
      if (!embFilled) return false
      const embOk = setupStatus.value?.embedding_ok === true
        || !!embTest.value?.ok
        || skipEmbeddingCheck.value
      if (!embOk) return false
      // 打标服务：仅在启用时校验；同样要求必填三件套 + 连通/跳过
      if (form.LABELING_ENABLED) {
        const labelFilled = !!(
          form.LABEL_API_URL
          && hasValueOrConfigured('LABEL_API_KEY')
          && form.LABEL_MODEL
        )
        if (!labelFilled) return false
        if (!hasConfigured('LABEL_API_KEY') && !labelTest.value?.ok && !skipLabelCheck.value) return false
      }
      return true
    }
    // 第 5 步：高级功能可选可跳过；开启视觉或联网搜索时强制填写关键字段
    case 5: {
      // 自定义（非复用打标）模式下必须填三件套
      if (!form.LIFE_REUSE_LABEL || !form.LABELING_ENABLED) {
        // 允许全留空（fallback 到主聊天模型，慢但能跑）。这里不强制
      }
      if (form.RESPONSE_POLICY_MODEL_ENABLED
          && (!form.RESPONSE_POLICY_REUSE_LABEL || !form.LABELING_ENABLED)) {
        if (!form.RESPONSE_POLICY_API_URL || !form.RESPONSE_POLICY_API_KEY || !form.RESPONSE_POLICY_MODEL) {
          return false
        }
      }
      if (form.VISION_ENABLED && !form.CHAT_MODEL_SUPPORTS_VISION) {
        if (!form.VISION_API_URL || !form.VISION_API_KEY || !form.VISION_MODEL) return false
      }
      if (form.WEB_ACCESS_ENABLED && form.WEB_SEARCH_PROVIDER === 'tavily' && !form.WEB_SEARCH_API_KEY) {
        return false
      }
      return true
    }
    case 6: {
      // 检索增强：全留空 = 跳过这步（用默认 RRF）。开了 cross-rerank 就要求关键字段齐全。
      if (form.CROSS_RERANK_ENABLED) {
        if (!form.CROSS_RERANK_API_URL || !form.CROSS_RERANK_API_KEY || !form.CROSS_RERANK_MODEL) {
          return false
        }
      }
      return true
    }
    case 7: {
      // 切分策略：勾了 adaptive + 模型版 + 非复用打标 → 三件套必填
      if (form.CHUNKING_STRATEGY === 'adaptive'
          && form.ADAPTIVE_CHUNK_MODEL_ENABLED
          && !form.ADAPTIVE_CHUNK_REUSE_LABEL) {
        if (!form.ADAPTIVE_CHUNK_API_URL || !form.ADAPTIVE_CHUNK_API_KEY || !form.ADAPTIVE_CHUNK_MODEL) {
          return false
        }
      }
      // 完成或没文件可跳过 → 直接放行；失败/取消时也允许进入下一步（用户已经看到错误，自己决定）
      const st = importTask.value?.status
      if (st === 'done') return true
      if (uploadedFiles.value.length === 0) return true
      if (st === 'failed' || st === 'cancelled') return true
      return false
    }
    default: return true
  }
})

const currentPresetApplyUrl = computed(() => {
  if (form.CHAT_API_PROTOCOL !== 'chat_completions') return undefined
  return chatPresets.value.find(p => p.id !== 'custom' && p.base_url === form.OPENAI_BASE_URL)?.apply_url
})

// 选中态判定：先匹配真实 preset，匹配不到（base_url 是用户自填的）则视为自定义中转站
const currentChatPresetId = computed(() => {
  if (form.CHAT_API_PROTOCOL !== 'chat_completions') {
    return form.OPENAI_BASE_URL ? 'custom' : ''
  }
  const match = chatPresets.value.find(p => p.id !== 'custom' && p.base_url === form.OPENAI_BASE_URL)
  if (match) return match.id
  if (form.OPENAI_BASE_URL) return 'custom'
  return ''
})

const currentEmbPresetId = computed(() => {
  const match = embPresets.value.find(
    p => p.id !== 'custom'
      && p.base_url === form.EMBEDDING_API_URL
      && p.default_model === form.EMBEDDING_MODEL,
  )
  if (match) return match.id
  if (form.EMBEDDING_API_URL) return 'custom'
  return ''
})

const currentEmbPresetApplyUrl = computed(() => {
  return embPresets.value.find(p => p.id !== 'custom' && p.base_url === form.EMBEDDING_API_URL)?.apply_url
})

const currentLabelPresetId = computed(() => {
  const match = labelPresets.value.find(p => p.id !== 'custom' && p.base_url === form.LABEL_API_URL)
  if (match) return match.id
  if (form.LABEL_API_URL) return 'custom'
  return ''
})

const currentLabelPresetApplyUrl = computed(() => {
  return labelPresets.value.find(p => p.id !== 'custom' && p.base_url === form.LABEL_API_URL)?.apply_url
})

// 把后端 stage 文案 "正在处理 xx.json（1/3） · 已用时 0:42" 拆成两段方便 UI 突出显示。
// 拆不出来时整体当主文案。
const importStageMain = computed(() => {
  const s = importTask.value?.stage || ''
  const idx = s.lastIndexOf(' · 已用时 ')
  return translateBackendText(idx >= 0 ? s.slice(0, idx) : s)
})
const importStageElapsed = computed(() => {
  const s = importTask.value?.stage || ''
  const idx = s.lastIndexOf(' · 已用时 ')
  return idx >= 0 ? t('elapsed', { time: s.slice(idx + ' · 已用时 '.length) }) : ''
})
const importStatusTitle = computed(() => {
  const st = importTask.value?.status
  switch (st) {
    case 'done': return importTask.value?.error ? t('importCompleteWithWarnings') : t('importComplete')
    case 'failed': return t('importFailed')
    case 'cancelled': return t('cancelled')
    case 'persona': return t('generatingPersonaProfile')
    case 'importing': return t('embeddingAndStoringRecords')
    default: return t('preparing')
  }
})

onMounted(async () => {
  // 恢复持久化的上传列表
  const persisted = loadPersistedFiles()
  if (persisted.length) uploadedFiles.value = persisted

  // 有保存的访问令牌 → 自动校验、进入向导并恢复未完成的导入（在 checkToken 内部）
  // 没有访问令牌或令牌失效时停在第 0 步，等待用户手动粘贴。
  if (tokenInput.value) await checkToken()
})
</script>

<template>
  <div class="paper-bg min-h-screen">
    <div class="max-w-2xl mx-auto px-4 py-8 sm:py-12">
      <div class="flex justify-end mb-3">
        <label class="sr-only" for="config-language-switcher">{{ t('language') }}</label>
        <select id="config-language-switcher" v-model="locale" @change="setLocale(locale)"
          class="rounded-lg bg-paper-soft dark:bg-night-bg-soft border border-ink/10 dark:border-night-text/10 px-2 py-1 text-xs">
          <option value="zh-CN">{{ t('chinese') }}</option>
          <option value="en">{{ t('english') }}</option>
        </select>
      </div>
      <!-- 完成页 -->
      <div v-if="finished" class="text-center py-12 space-y-5">
        <div class="inline-flex w-16 h-16 rounded-full bg-accent/15 text-accent
                    dark:bg-night-accent/15 dark:text-night-accent items-center justify-center">
          <CheckCircle2 :size="36" />
        </div>
        <h1 class="text-2xl font-medium">{{ t('configurationSaved') }}</h1>
        <p class="text-sm text-ink-soft dark:text-night-text-soft max-w-md mx-auto leading-relaxed">
          {{ t('allChangesWereWrittenToBackendEnv') }}<br />
          <code class="font-mono text-xs px-2 py-0.5 rounded bg-paper-shade dark:bg-night-bg-soft">
            {{ t('pressCtrlCThenRunTheStart') }}
          </code>
        </p>
        <p v-if="backupPath" class="text-xs text-ink-soft dark:text-night-text-soft">
          {{ t('previousEnvBackedUpTo') }} <code class="font-mono">{{ backupPath }}</code>
        </p>
        <p class="text-xs text-ink-soft dark:text-night-text-soft pt-4">
          {{ t('youCanNowCloseThisPage') }}
        </p>
        <button type="button"
          class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm
                 border border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg"
          @click="goToStep(-1)">
          <Settings2 :size="14" /> {{ t('backToOverview') }}
        </button>
      </div>

      <template v-else>
        <header class="mb-8 text-center">
          <h1 class="text-2xl font-medium tracking-wide">{{ t('title') }}</h1>
          <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
            {{ step === -1 ? t('reviewYourConfigurationAndOpenTheSection') : t('configurationSavesAutomaticallyYouCanSwitchSections') }}
          </p>
        </header>

        <!-- 进度条 -->
        <div v-if="step > 0" class="mb-8">
          <div class="flex items-center justify-between text-xs text-ink-soft dark:text-night-text-soft mb-2">
            <span>{{ t('stepProgress', { current: step, total: totalSteps }) }}</span>
            <span>{{ progress }}%</span>
          </div>
          <div class="h-1 rounded-full bg-ink/5 dark:bg-night-text/10 overflow-hidden">
            <div
              class="h-full bg-accent dark:bg-night-accent transition-all duration-500"
              :style="{ width: `${progress}%` }"
            />
          </div>
        </div>

        <nav v-if="step !== 0" class="mb-6 space-y-2">
          <button type="button"
            class="inline-flex items-center gap-1.5 text-xs text-ink-soft dark:text-night-text-soft
                   hover:text-ink dark:hover:text-night-text"
            @click="goToStep(-1)">
            <Settings2 :size="13" /> {{ t('configurationOverview') }}
          </button>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <button v-for="item in stepItems" :key="item.id" type="button"
              class="min-h-12 px-3 py-2 text-left rounded-lg border text-xs transition-colors"
              :class="step === item.id
                ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                : 'border-ink/10 dark:border-night-text/10 hover:border-ink/30 dark:hover:border-night-text/30'"
              @click="goToStep(item.id)">
              <span class="block font-medium">{{ item.id }}. {{ item.label }}</span>
              <span class="mt-0.5 block text-[11px] text-ink-soft dark:text-night-text-soft">
                {{ item.optional ? t('optional') : item.complete ? t('configured') : t('notConfigured') }}
              </span>
            </button>
          </div>
        </nav>

        <section class="rounded-2xl bg-paper-soft dark:bg-night-bg-soft shadow-letter
                        border border-ink/5 dark:border-night-text/10 p-6 sm:p-8">

          <!-- 配置总览 -->
          <div v-if="step === -1" class="space-y-5">
            <div class="flex items-center gap-2 text-accent dark:text-night-accent">
              <Settings2 :size="18" />
              <h2 class="text-lg font-medium">{{ t('configurationOverview') }}</h2>
            </div>
            <p class="text-sm leading-relaxed text-ink-soft dark:text-night-text-soft">
              {{ t('existingSettingsArePreservedChangeOnlyThe') }}
            </p>
            <div class="divide-y divide-ink/5 dark:divide-night-text/10 border-y border-ink/5 dark:border-night-text/10">
              <button v-for="item in stepItems" :key="item.id" type="button"
                class="w-full flex items-center gap-3 py-3 text-left hover:text-accent dark:hover:text-night-accent"
                @click="goToStep(item.id)">
                <span class="w-6 font-mono text-xs text-ink-soft dark:text-night-text-soft">{{ item.id }}</span>
                <span class="flex-1 text-sm font-medium">{{ item.label }}</span>
                <span class="text-xs" :class="item.complete || item.optional
                  ? 'text-accent dark:text-night-accent'
                  : 'text-warning'">
                  {{ item.optional ? t('optional') : item.complete ? t('configured') : t('notConfigured') }}
                </span>
                <ArrowRight :size="14" />
              </button>
            </div>
          </div>

          <!-- 第 0 步：访问令牌 -->
          <div v-else-if="step === 0" class="space-y-5">
            <div class="flex items-center gap-2 text-accent dark:text-night-accent">
              <KeyRound :size="18" />
              <h2 class="text-lg font-medium">{{ t('enterAccessToken') }}</h2>
            </div>
            <p class="text-sm leading-relaxed text-ink-soft dark:text-night-text-soft">
              {{ t('forInitialSetupCopyTheOneTime') }}
            </p>
            <pre class="text-xs p-3 rounded-lg bg-paper dark:bg-night-bg
                        border border-ink/10 dark:border-night-text/10 overflow-x-auto"
            >{{ t('configurationUIEnabledNPathConfigNAccessToken') }}</pre>
            <label class="block">
              <span class="text-sm">{{ t('pasteTheTokenHere') }}</span>
              <input
                v-model="tokenInput"
                type="password"
                autocomplete="off"
                class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                       border border-ink/10 dark:border-night-text/10 outline-none
                       focus:ring-2 focus:ring-accent-soft font-mono text-sm"
                @keyup.enter="checkToken"
              />
            </label>
            <p v-if="tokenError" class="text-sm text-warning flex items-center gap-1.5">
              <AlertCircle :size="14" /> {{ translateBackendText(tokenError) }}
            </p>
            <button
              class="w-full py-2.5 rounded-full bg-accent text-paper-soft
                     hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed
                     inline-flex items-center justify-center gap-2"
              :disabled="tokenChecking || !tokenInput.trim()"
              @click="checkToken"
            >
              <Loader2 v-if="tokenChecking" :size="16" class="animate-spin" />
              <span>{{ tokenChecking ? t('checking') : t('continue') }}</span>
            </button>
          </div>

          <!-- 第 1 步：身份 -->
          <div v-else-if="step === 1" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">{{ t('yourIdentityAndTheirs') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                {{ t('theseDetailsIdentifyWhichMessagesAreYours') }}
              </p>
            </div>

            <!-- 从聊天文件识别 -->
            <div class="rounded-xl bg-paper dark:bg-night-bg
                        border border-accent/30 dark:border-night-accent/30 p-4 space-y-3">
              <div class="flex items-start gap-2">
                <Wand2 :size="16" class="mt-0.5 text-accent dark:text-night-accent flex-shrink-0" />
                <div class="flex-1">
                  <div class="text-sm font-medium">{{ t('identifyFromChatFilesRecommended') }}</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    {{ t('chooseAfterglowChatQQWeChatOrDouyin') }}
                  </div>
                </div>
              </div>
              <label class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs
                            bg-accent text-paper-soft cursor-pointer hover:bg-accent/90
                            disabled:opacity-50">
                <input type="file" accept="application/json,application/x-ndjson,.json,.jsonl,.ndjson,.txt,.md,.html,.htm" multiple class="hidden"
                  @change="onIdentifyFilesPicked" :disabled="inspecting" />
                <Loader2 v-if="inspecting" :size="12" class="animate-spin" />
                <Upload v-else :size="12" />
                <span>{{ inspecting ? t('uploadingAndParsing') : t('chooseChatFiles') }}</span>
              </label>
              <p v-if="inspectError" class="text-xs text-warning flex items-center gap-1">
                <AlertCircle :size="12" /> {{ translateBackendText(inspectError) }}
              </p>

              <!-- 已上传文件 -->
              <div v-if="uploadedFiles.length" class="space-y-1.5 pt-1">
                <div class="text-xs text-ink-soft dark:text-night-text-soft">
                  {{ t('uploaded') }} {{ uploadedFiles.length }} {{ t('filesReadyForTheImportStep') }}
                  <span v-if="uploadedFiles.length > 1">
                    {{ t('allFilesWillBeCombinedToGenerate') }}
                  </span>
                </div>
                <div v-for="(f, i) in uploadedFiles" :key="f.saved_as"
                  class="flex items-center gap-2 px-2.5 py-1.5 rounded-md
                         bg-paper-soft dark:bg-night-bg-soft
                         border border-ink/5 dark:border-night-text/10 text-xs">
                  <FileText :size="14" class="text-ink-soft dark:text-night-text-soft flex-shrink-0" />
                  <span class="truncate flex-1">{{ f.name }}</span>
                  <span class="text-ink-soft dark:text-night-text-soft whitespace-nowrap">
                    {{ translateBackendText(f.format_label || f.format || '?') }}
                    · {{ f.total_messages || 0 }} {{ t('messages') }}
                  </span>
                  <button type="button" @click="removeUploaded(i)"
                    class="p-0.5 text-ink-soft dark:text-night-text-soft hover:text-warning">
                    <X :size="12" />
                  </button>
                </div>
              </div>

              <!-- 候选列表 -->
              <div v-if="inspectCandidates.length" class="space-y-2 pt-1">
                <div class="text-xs text-ink-soft dark:text-night-text-soft leading-relaxed">
                  {{ t('candidateHelp', { count: inspectCandidates.length }) }}
                </div>
                <div v-for="c in inspectCandidates" :key="c.uid"
                  class="flex items-center gap-3 p-2.5 rounded-lg
                         border transition-colors"
                  :class="(isPickedAsSelf(c) || isPickedAsFriend(c))
                    ? 'bg-paper-soft dark:bg-night-bg-soft border-ink/5 dark:border-night-text/10'
                    : 'bg-warning/5 border-warning/30 dark:bg-warning/10'">
                  <UserCircle2 :size="20" class="text-ink-soft dark:text-night-text-soft" />
                  <div class="flex-1 min-w-0">
                    <div class="text-sm font-medium truncate">{{ c.name }}</div>
                    <div class="text-xs text-ink-soft dark:text-night-text-soft font-mono truncate">
                      {{ c.uid }}
                    </div>
                  </div>
                  <button type="button" @click="pickAsSelf(c)"
                    class="text-xs px-2.5 py-1 rounded-full border whitespace-nowrap"
                    :class="isPickedAsSelf(c)
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper-shade dark:hover:bg-night-bg-soft'">
                    {{ t('assignToMe') }}
                  </button>
                  <button type="button" @click="pickAsFriend(c)"
                    class="text-xs px-2.5 py-1 rounded-full border whitespace-nowrap"
                    :class="isPickedAsFriend(c)
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper-shade dark:hover:bg-night-bg-soft'">
                    {{ t('assignToFriend') }}
                  </button>
                </div>
                <div v-if="unassignedCandidates.length > 0"
                  class="rounded-lg px-3.5 py-2.5 text-xs leading-relaxed
                         bg-warning/10 border border-warning/30 text-warning">
                  {{ t('unassignedHelp', { count: unassignedCandidates.length, uids: unassignedCandidates.map(c => c.uid).join(', ') }) }}
                </div>
              </div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <label class="block">
                <span class="text-sm">{{ t('yourNickname') }}</span>
                <input v-model="form.SELF_NAME"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft" />
              </label>
              <label class="block">
                <span class="text-sm">{{ t('yourAccountID') }}</span>
                <input v-model="form.SELF_UID" :placeholder="t('qqUXxxWeChatWxidXxx')"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <label class="block">
                <span class="text-sm">{{ t('theirNickname') }}</span>
                <input v-model="form.FRIEND_NAME"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft" />
              </label>
              <label class="block">
                <span class="text-sm">{{ t('theirAccountID') }}</span>
                <input v-model="form.FRIEND_UID" :placeholder="t('separateMultipleAccountsWithCommas')"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
            </div>
            <p class="text-xs text-ink-soft dark:text-night-text-soft">
              {{ t('youCanEnterDetailsManuallyInsteadFor') }}
            </p>
          </div>

          <!-- 第 2 步：关系 -->
          <div v-else-if="step === 2" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">{{ t('yourRelationship') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">{{ t('usedToSelectASuitableToneTemplate') }}</p>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <button v-for="r in relationships" :key="r.value"
                class="py-4 rounded-xl border text-sm transition-colors"
                :class="form.RELATIONSHIP_TYPE === r.value
                  ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                  : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                @click="form.RELATIONSHIP_TYPE = r.value">
                {{ r.label }}
              </button>
            </div>
            <label v-if="form.RELATIONSHIP_TYPE === 'custom'" class="block">
              <span class="text-sm">{{ t('describeTheRelationship') }}</span>
              <input v-model="form.RELATIONSHIP_DESCRIPTION"
                :placeholder="t('forExampleSchoolFriendsOrChildhoodFriends')"
                class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                       border border-ink/10 dark:border-night-text/10 outline-none
                       focus:ring-2 focus:ring-accent-soft" />
            </label>
          </div>

          <!-- 第 3 步：聊天 AI -->
          <div v-else-if="step === 3" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">{{ t('chooseAChatAI') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                {{ t('generatesConversationRepliesEnterYourProviderDetails') }}
              </p>
            </div>

            <div class="rounded-lg px-3.5 py-2.5 text-xs leading-relaxed
                        bg-accent/5 dark:bg-night-accent/10
                        border border-accent/20 dark:border-night-accent/25
                        text-ink-soft dark:text-night-text-soft">
              {{ t('considerLargerExpressiveModelsSuchAsDeepSeek') }}
            </div>

            <div v-if="isChinese" class="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <button v-for="p in chatPresets" :key="p.id"
                class="text-left p-3 rounded-lg border text-sm transition-colors"
                :class="currentChatPresetId === p.id
                  ? 'border-accent bg-accent/10 dark:border-night-accent dark:bg-night-accent/10'
                  : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                @click="applyChatPreset(p)">
                <div class="font-medium">{{ p.label }}</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5">{{ p.hint }}</div>
              </button>
            </div>
            <div class="space-y-3 pt-2">
              <label class="block">
                <span class="text-sm">{{ t('apiURL') }}</span>
                <input v-model="form.OPENAI_BASE_URL"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <label class="block">
                <div class="flex items-baseline justify-between">
                  <span class="text-sm">{{ t('apiKey') }}</span>
                  <a v-if="isChinese && currentPresetApplyUrl" :href="currentPresetApplyUrl"
                    target="_blank" rel="noopener"
                    class="text-xs text-accent dark:text-night-accent hover:underline inline-flex items-center gap-1">
                    {{ t('getAnAPIKey') }}<ExternalLink :size="11" />
                  </a>
                </div>
                <input v-model="form.OPENAI_API_KEY" type="password"
                  :placeholder="hasConfigured('OPENAI_API_KEY') ? t('alreadyConfiguredLeaveBlankToKeepIt') : ''"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <label class="block">
                <span class="text-sm">{{ t('apiProtocol') }}</span>
                <select v-model="form.CHAT_API_PROTOCOL"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm">
                  <option value="chat_completions">{{ t('chatCompletionsWidestCompatibility') }}</option>
                  <option value="responses">{{ t('responsesForProvidersRequiringThisEndpoint') }}</option>
                </select>
              </label>
              <label class="block">
                <span class="text-sm">{{ t('modelName') }}</span>
                <input v-model="form.CHAT_MODEL"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
            </div>
            <div class="flex items-center gap-3 pt-1 flex-wrap">
              <button
                class="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full text-sm
                       border border-ink/10 dark:border-night-text/10
                       bg-paper dark:bg-night-bg hover:bg-paper-shade dark:hover:bg-night-bg-soft
                       disabled:opacity-40"
                :disabled="chatTesting || !form.OPENAI_BASE_URL || !form.OPENAI_API_KEY || !form.CHAT_MODEL"
                @click="testChat">
                <Loader2 v-if="chatTesting" :size="14" class="animate-spin" />
                <RefreshCw v-else :size="14" />
                <span>{{ chatTesting ? t('testing') : t('testConnection') }}</span>
              </button>
              <div v-if="chatTest" class="text-sm flex items-center gap-1.5"
                :class="chatTest.ok ? 'text-accent dark:text-night-accent' : 'text-warning'">
                <CheckCircle2 v-if="chatTest.ok" :size="14" />
                <AlertCircle v-else :size="14" />
                <span>{{ translateBackendText(chatTest.message?.trim() || t('unknownErrorCheckYourNetworkProxyAnd')) }}</span>
              </div>
            </div>
            <!-- 允许跳过：连通测试失败时给一个"确认无误强制继续"的出口 -->
            <label v-if="chatTest && !chatTest.ok"
              class="flex items-center gap-2 mt-1 cursor-pointer text-xs text-warning/90">
              <input v-model="skipChatCheck" type="checkbox" class="w-3.5 h-3.5 accent-warning" />
              <span>{{ t('theTestFailedButIHaveChecked') }}</span>
            </label>
          </div>

          <!-- 第 4 步：向量服务 + 打标 -->
          <div v-else-if="step === 4" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">{{ t('chooseAnEmbeddingService') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                {{ t('convertsChatHistoryIntoVectorsConsiderA') }}
              </p>
            </div>
            <div v-if="isChinese" class="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <button v-for="p in embPresets" :key="p.id"
                class="text-left p-3 rounded-lg border text-sm transition-colors"
                :class="currentEmbPresetId === p.id
                  ? 'border-accent bg-accent/10 dark:border-night-accent dark:bg-night-accent/10'
                  : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                @click="applyEmbPreset(p)">
                <div class="font-medium">{{ p.label }}</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5">{{ p.hint }}</div>
              </button>
            </div>
            <div class="space-y-3 pt-2">
              <label class="block">
                <span class="text-sm">{{ t('apiURL') }}</span>
                <input v-model="form.EMBEDDING_API_URL"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <label class="block">
                <div class="flex items-baseline justify-between">
                  <span class="text-sm">{{ t('apiKey') }}</span>
                  <a v-if="isChinese && currentEmbPresetApplyUrl" :href="currentEmbPresetApplyUrl"
                    target="_blank" rel="noopener"
                    class="text-xs text-accent dark:text-night-accent hover:underline inline-flex items-center gap-1">
                    {{ t('getAnAPIKey') }}<ExternalLink :size="11" />
                  </a>
                </div>
                <input v-model="form.EMBEDDING_API_KEY" type="password"
                  :placeholder="hasConfigured('EMBEDDING_API_KEY') ? t('alreadyConfiguredLeaveBlankToKeepIt') : ''"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <div class="grid grid-cols-3 gap-3">
                <label class="block col-span-2">
                  <span class="text-sm">{{ t('modelName') }}</span>
                  <input v-model="form.EMBEDDING_MODEL"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">{{ t('vectorDimensions') }}</span>
                  <input v-model.number="form.EMBEDDING_DIM" type="number"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
              </div>
            </div>
            <div class="flex items-center gap-3 pt-1 flex-wrap">
              <button
                class="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full text-sm
                       border border-ink/10 dark:border-night-text/10
                       bg-paper dark:bg-night-bg hover:bg-paper-shade dark:hover:bg-night-bg-soft
                       disabled:opacity-40"
                :disabled="embTesting || !form.EMBEDDING_API_URL || !form.EMBEDDING_API_KEY || !form.EMBEDDING_MODEL"
                @click="testEmbedding">
                <Loader2 v-if="embTesting" :size="14" class="animate-spin" />
                <RefreshCw v-else :size="14" />
                <span>{{ embTesting ? t('testing') : t('testConnection') }}</span>
              </button>
              <div v-if="embTest" class="text-sm flex items-center gap-1.5"
                :class="embTest.ok ? 'text-accent dark:text-night-accent' : 'text-warning'">
                <CheckCircle2 v-if="embTest.ok" :size="14" />
                <AlertCircle v-else :size="14" />
                <span>{{ translateBackendText(embTest.message?.trim() || t('unknownErrorCheckYourNetworkProxyAnd')) }}</span>
              </div>
              <button v-if="embTest && !embTest.ok && embTest.extra?.actual_dim"
                class="text-xs px-3 py-1 rounded-full border border-accent/40 text-accent
                       dark:text-night-accent hover:bg-accent/10"
                @click="acceptActualDim">
                {{ t('useActualDimensions') }} {{ embTest.extra.actual_dim }}
              </button>
            </div>

            <!-- 打标推荐 -->
            <div class="mt-6 pt-5 border-t border-ink/5 dark:border-night-text/10 space-y-3">
              <label class="flex items-start gap-3 cursor-pointer">
                <input v-model="form.LABELING_ENABLED" type="checkbox"
                  class="mt-1 w-4 h-4 accent-accent dark:accent-night-accent" />
                <div>
                  <div class="text-sm font-medium">
                    {{ t('enableSemanticLabeling') }}
                    <span class="text-xs text-accent dark:text-night-accent ml-1">{{ t('recommended') }}</span>
                  </div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    {{ t('aSmallModelLabelsEachMessageWith') }}
                  </div>
                </div>
              </label>
              <div v-if="form.LABELING_ENABLED" class="ml-7 space-y-3">
                <div v-if="isChinese" class="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <button v-for="p in labelPresets" :key="p.id"
                    class="text-left p-3 rounded-lg border text-xs transition-colors"
                    :class="currentLabelPresetId === p.id
                      ? 'border-accent bg-accent/10 dark:border-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                    @click="applyLabelPreset(p)">
                    <div class="font-medium">{{ p.label }}</div>
                    <div class="text-ink-soft dark:text-night-text-soft mt-0.5">{{ p.hint }}</div>
                  </button>
                </div>
                <label class="block">
                  <span class="text-sm">{{ t('apiURL') }}</span>
                  <input v-model="form.LABEL_API_URL"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <div class="flex items-baseline justify-between">
                    <span class="text-sm">{{ t('apiKey') }}</span>
                    <a v-if="isChinese && currentLabelPresetApplyUrl" :href="currentLabelPresetApplyUrl"
                      target="_blank" rel="noopener"
                      class="text-xs text-accent dark:text-night-accent hover:underline inline-flex items-center gap-1">
                      {{ t('getAnAPIKey') }}<ExternalLink :size="11" />
                    </a>
                  </div>
                  <input v-model="form.LABEL_API_KEY" type="password"
                    :placeholder="hasConfigured('LABEL_API_KEY') ? t('alreadyConfiguredLeaveBlankToKeepIt') : ''"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">{{ t('modelName') }}</span>
                  <input v-model="form.LABEL_MODEL"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">{{ t('labelingConcurrency') }}</span>
                  <div class="mt-1 flex items-center gap-3">
                    <input v-model.number="form.LABEL_MAX_CONCURRENCY"
                      type="number" min="1" max="50"
                      class="w-24 px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm text-center" />
                    <span class="text-xs text-ink-soft dark:text-night-text-soft leading-snug">
                      {{ t('theFreeGLM4FlashLimitIs') }}
                    </span>
                  </div>
                </label>
                <div class="flex items-center gap-3 flex-wrap">
                  <button
                    class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs
                           border border-ink/10 dark:border-night-text/10
                           hover:bg-paper dark:hover:bg-night-bg disabled:opacity-40"
                    :disabled="labelTesting || !form.LABEL_API_URL || !form.LABEL_API_KEY || !form.LABEL_MODEL"
                    @click="testLabel">
                    <Loader2 v-if="labelTesting" :size="12" class="animate-spin" />
                    <RefreshCw v-else :size="12" />
                    <span>{{ labelTesting ? t('testing') : t('testLabelingService') }}</span>
                  </button>
                  <div v-if="labelTest" class="text-xs flex items-center gap-1"
                    :class="labelTest.ok ? 'text-accent dark:text-night-accent' : 'text-warning'">
                    <CheckCircle2 v-if="labelTest.ok" :size="12" />
                    <AlertCircle v-else :size="12" />
                    <span>{{ translateBackendText(labelTest.message?.trim() || t('unknownErrorCheckYourNetworkProxyAnd')) }}</span>
                  </div>
                </div>
                <!-- 打标服务独立的跳过开关；仅在启用打标且校验失败时出现 -->
                <label v-if="form.LABELING_ENABLED && labelTest && !labelTest.ok"
                  class="flex items-center gap-2 mt-1 cursor-pointer text-xs text-warning/90">
                  <input v-model="skipLabelCheck" type="checkbox" class="w-3.5 h-3.5 accent-warning" />
                  <span>{{ t('theLabelingTestFailedButIHave') }}</span>
                </label>
              </div>
            </div>
            <!-- 允许跳过：仅放行 embedding 的连通性失败；必填字段未填仍会被 canNext 拦下 -->
            <label v-if="embTest && !embTest.ok"
              class="flex items-center gap-2 mt-1 cursor-pointer text-xs text-warning/90">
              <input v-model="skipEmbeddingCheck" type="checkbox" class="w-3.5 h-3.5 accent-warning" />
              <span>{{ t('theEmbeddingTestFailedButIHave') }}</span>
            </label>
          </div>

          <!-- 第 5 步：可选功能 -->
          <div v-else-if="step === 5" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">{{ t('optionalFeatures') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                {{ t('theseFeaturesAddContextToConversationsYou') }}
              </p>
            </div>

            <!-- 重要：小功能模型选择策略提示 -->
            <div class="rounded-lg px-3.5 py-2.5 text-xs leading-relaxed
                        bg-accent/5 dark:bg-night-accent/10
                        border border-accent/20 dark:border-night-accent/25
                        text-ink-soft dark:text-night-text-soft">
              <span class="font-medium text-ink dark:text-night-text">{{ t('speedRecommendation') }}</span>
              {{ t('reuseTheLabelingModelSuchAsGLM') }}
            </div>

            <!-- 生活时间线（必备，但可选复用方式） -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3">
              <div>
                <div class="text-sm font-medium">{{ t('lifeTimeline') }}</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                  {{ t('givesTheAIADailyRoutineWaking') }}
                </div>
              </div>
              <div class="flex gap-2">
                <button type="button"
                  :disabled="!form.LABELING_ENABLED"
                  class="flex-1 py-2 rounded-lg border text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  :class="form.LIFE_REUSE_LABEL && form.LABELING_ENABLED
                    ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                    : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                  @click="form.LIFE_REUSE_LABEL = true">
                  {{ t('reuseLabelingModelRecommended') }}
                </button>
                <button type="button"
                  class="flex-1 py-2 rounded-lg border text-sm transition-colors"
                  :class="!form.LIFE_REUSE_LABEL || !form.LABELING_ENABLED
                    ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                    : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                  @click="form.LIFE_REUSE_LABEL = false">
                  {{ t('customModel') }}
                </button>
              </div>
              <div v-if="form.LIFE_REUSE_LABEL && form.LABELING_ENABLED"
                class="text-xs text-ink-soft dark:text-night-text-soft px-1">
                {{ t('willUse') }} <code class="font-mono">{{ form.LABEL_MODEL || t('labelingModel') }}</code>
                {{ t('fromTheLabelingSettingsInThePrevious') }}
              </div>
              <div v-else-if="form.LIFE_REUSE_LABEL && !form.LABELING_ENABLED"
                class="text-xs text-warning px-1">
                {{ t('labelingIsDisabledSoItsModelCannot') }}
              </div>
              <div v-if="!form.LIFE_REUSE_LABEL || !form.LABELING_ENABLED" class="space-y-3">
                <div class="text-xs text-ink-soft dark:text-night-text-soft px-1">
                  {{ t('ifAllFieldsAreBlankTheMain') }}
                </div>
                <label class="block">
                  <span class="text-sm">{{ t('apiURL') }}</span>
                  <input v-model="form.LIFE_API_URL" :placeholder="t('eGHttpsOpenBigmodelCnApi')"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">{{ t('apiKey') }}</span>
                  <input v-model="form.LIFE_API_KEY" type="password"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">{{ t('modelName') }}</span>
                  <input v-model="form.LIFE_MODEL" :placeholder="t('eGGlm4Flash')"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
              </div>
            </div>

            <!-- 互动决策（可选） -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3">
              <label class="flex items-start gap-3 cursor-pointer">
                <input v-model="form.RESPONSE_POLICY_MODEL_ENABLED" type="checkbox"
                  class="mt-1 w-4 h-4 accent-accent dark:accent-night-accent" />
                <div class="flex-1">
                  <div class="text-sm font-medium">{{ t('interactionDecisionModel') }}</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    {{ t('theRuleEngineSelectsAResponseStyle') }}
                  </div>
                </div>
              </label>
              <div v-if="form.RESPONSE_POLICY_MODEL_ENABLED" class="space-y-3">
                <div class="flex gap-2">
                  <button type="button"
                    :disabled="!form.LABELING_ENABLED"
                    class="flex-1 py-2 rounded-lg border text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    :class="form.RESPONSE_POLICY_REUSE_LABEL && form.LABELING_ENABLED
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                    @click="form.RESPONSE_POLICY_REUSE_LABEL = true">
                    {{ t('reuseLabelingModelRecommended') }}
                  </button>
                  <button type="button"
                    class="flex-1 py-2 rounded-lg border text-sm transition-colors"
                    :class="!form.RESPONSE_POLICY_REUSE_LABEL || !form.LABELING_ENABLED
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                    @click="form.RESPONSE_POLICY_REUSE_LABEL = false">
                    {{ t('customModel') }}
                  </button>
                </div>
                <div v-if="form.RESPONSE_POLICY_REUSE_LABEL && form.LABELING_ENABLED"
                  class="text-xs text-ink-soft dark:text-night-text-soft px-1">
                  {{ t('willUse') }} <code class="font-mono">{{ form.LABEL_MODEL || t('labelingModel') }}</code>
                </div>
                <div v-if="!form.RESPONSE_POLICY_REUSE_LABEL || !form.LABELING_ENABLED" class="space-y-3">
                  <label class="block">
                    <span class="text-sm">{{ t('apiURL') }}</span>
                    <input v-model="form.RESPONSE_POLICY_API_URL"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                  <label class="block">
                    <span class="text-sm">{{ t('apiKey') }}</span>
                    <input v-model="form.RESPONSE_POLICY_API_KEY" type="password"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                  <label class="block">
                    <span class="text-sm">{{ t('modelName') }}</span>
                    <input v-model="form.RESPONSE_POLICY_MODEL"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                </div>
              </div>
            </div>

            <!-- 视觉理解 -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3">
              <label class="flex items-start gap-3 cursor-pointer">
                <input v-model="form.VISION_ENABLED" type="checkbox"
                  class="mt-1 w-4 h-4 accent-accent dark:accent-night-accent" />
                <div class="flex-1">
                  <div class="text-sm font-medium">{{ t('imageUnderstanding') }}</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    {{ t('enablesImageMessagesIfTheMainChat') }}
                  </div>
                </div>
              </label>
              <div v-if="form.VISION_ENABLED" class="ml-7 space-y-3">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input v-model="form.CHAT_MODEL_SUPPORTS_VISION" type="checkbox"
                    class="w-4 h-4 accent-accent dark:accent-night-accent" />
                  <span class="text-sm">{{ t('theMainChatModelSupportsVisionE') }}</span>
                </label>
                <div v-if="!form.CHAT_MODEL_SUPPORTS_VISION" class="space-y-3">
                  <label class="block">
                    <span class="text-sm">{{ t('visionAPIURL') }}</span>
                    <input v-model="form.VISION_API_URL"
                      :placeholder="t('eGHttpsDashscopeAliyuncsComCompatible')"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                  <label class="block">
                    <span class="text-sm">{{ t('apiKey') }}</span>
                    <input v-model="form.VISION_API_KEY" type="password"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                  <label class="block">
                    <span class="text-sm">{{ t('modelName') }}</span>
                    <input v-model="form.VISION_MODEL" :placeholder="t('eGQwenVlPlus')"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                </div>
              </div>
            </div>

            <!-- 联网搜索 -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3">
              <label class="flex items-start gap-3 cursor-pointer">
                <input v-model="form.WEB_ACCESS_ENABLED" type="checkbox"
                  class="mt-1 w-4 h-4 accent-accent dark:accent-night-accent" />
                <div class="flex-1">
                  <div class="text-sm font-medium">{{ t('webSearch') }}</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    {{ t('whenYouExplicitlyRequestASearchOr') }}
                  </div>
                </div>
              </label>
              <div v-if="form.WEB_ACCESS_ENABLED" class="ml-7 space-y-3">
                <div class="flex gap-2">
                  <button type="button" @click="() => { form.WEB_SEARCH_PROVIDER = 'tavily'; form.WEB_SEARCH_BASE_URL = 'https://api.tavily.com' }"
                    class="flex-1 py-2 rounded-lg border text-sm transition-colors"
                    :class="form.WEB_SEARCH_PROVIDER === 'tavily'
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'">
                    {{ t('tavilyMonthlyFreeAllowance') }}
                  </button>
                  <button type="button" @click="() => { form.WEB_SEARCH_PROVIDER = 'searxng'; form.WEB_SEARCH_BASE_URL = '' }"
                    class="flex-1 py-2 rounded-lg border text-sm transition-colors"
                    :class="form.WEB_SEARCH_PROVIDER === 'searxng'
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'">
                    {{ t('selfHostedSearXNG') }}
                  </button>
                </div>
                <label class="block">
                  <div class="flex items-baseline justify-between">
                    <span class="text-sm">{{ t('apiURL') }}</span>
                    <a v-if="form.WEB_SEARCH_PROVIDER === 'tavily'"
                      href="https://app.tavily.com/" target="_blank" rel="noopener"
                      class="text-xs text-accent dark:text-night-accent hover:underline inline-flex items-center gap-1">
                      {{ t('getAnAPIKey') }}<ExternalLink :size="11" />
                    </a>
                  </div>
                  <input v-model="form.WEB_SEARCH_BASE_URL"
                    :placeholder="form.WEB_SEARCH_PROVIDER === 'tavily' ? 'https://api.tavily.com' : t('yourSearXNGInstanceURL')"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label v-if="form.WEB_SEARCH_PROVIDER === 'tavily'" class="block">
                  <span class="text-sm">{{ t('tavilyAPIKey') }}</span>
                  <input v-model="form.WEB_SEARCH_API_KEY" type="password" placeholder="tvly-..."
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="flex items-center gap-2 cursor-pointer">
                  <input v-model="form.WEB_FETCH_ENABLED" type="checkbox"
                    class="w-4 h-4 accent-accent dark:accent-night-accent" />
                  <span class="text-sm">{{ t('readWebpageContentAutomaticallyWhenAMessage') }}</span>
                </label>
              </div>
            </div>
          </div>

          <!-- 第 6 步：检索增强（可选） -->
          <div v-else-if="step === 6" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">{{ t('retrievalEnhancementsOptional') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft leading-relaxed">
                {{ t('findsRelevantHistoricalMessagesBeforeGeneratingA') }}
              </p>
            </div>

            <!-- Query Rewrite -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3">
              <label class="flex items-start gap-3 cursor-pointer">
                <input v-model="form.QUERY_REWRITE_ENABLED" type="checkbox"
                  class="mt-1 w-4 h-4 accent-accent dark:accent-night-accent" />
                <div class="flex-1">
                  <div class="text-sm font-medium">{{ t('queryRewriting') }}</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    {{ t('rewritesShortMessagesSuchAsAreYou') }}
                  </div>
                </div>
              </label>
            </div>

            <!-- Cross-encoder 粗排 -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3">
              <label class="flex items-start gap-3 cursor-pointer">
                <input v-model="form.CROSS_RERANK_ENABLED" type="checkbox"
                  class="mt-1 w-4 h-4 accent-accent dark:accent-night-accent" />
                <div class="flex-1">
                  <div class="text-sm font-medium">{{ t('crossEncoderRerankingMorePreciseRetrieval') }}</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    {{ t('rerankingHelp') }}
                  </div>
                </div>
              </label>
              <div v-if="form.CROSS_RERANK_ENABLED" class="ml-7 space-y-3">
                <div v-if="isChinese" class="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <button v-for="p in crossRerankerPresets" :key="p.id" type="button"
                    @click="applyCrossRerankerPreset(p)"
                    :class="['text-left px-3 py-2 rounded-lg border text-xs',
                             form.CROSS_RERANK_API_URL === p.base_url
                               ? 'border-accent bg-accent/5 dark:border-night-accent dark:bg-night-accent/10'
                               : 'border-ink/10 dark:border-night-text/10 hover:border-ink/30']">
                    <div class="font-medium">{{ p.label }}</div>
                    <div class="text-ink-soft dark:text-night-text-soft mt-0.5">{{ p.hint }}</div>
                  </button>
                </div>

                <div class="text-xs text-ink-soft dark:text-night-text-soft">
                  {{ t('currentProtocol') }}<span class="font-mono">{{ form.CROSS_RERANK_PROTOCOL }}</span>
                  <span v-if="form.CROSS_RERANK_PROTOCOL === 'dashscope'">{{ t('nativeAlibabaDashScopeTextRerankAPI') }}</span>
                  <span v-else>{{ t('jinaCohereIFlytekSelfHostedBgeReranker') }}</span>
                </div>

                <label class="block">
                  <span class="text-sm">{{ t('apiURL') }}</span>
                  <input v-model="form.CROSS_RERANK_API_URL"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">{{ t('apiKey') }}</span>
                  <input v-model="form.CROSS_RERANK_API_KEY" type="password"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">{{ t('modelName') }}</span>
                  <input v-model="form.CROSS_RERANK_MODEL"
                    :placeholder="t('eGQwen3RerankXop3qwen8breranker')"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
              </div>
            </div>
          </div>

          <!-- 第 7 步：导入 -->
          <div v-else-if="step === 7" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">{{ t('importChatHistory') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                {{ t('checkTheFilesBelowBeforeImportingYou') }}
              </p>
            </div>

            <!-- 切分策略（影响下一次导入；对已入库 chunks 无效） -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3"
              :class="{ 'opacity-60 pointer-events-none': chunkingStrategyLocked }">
              <div>
                <div class="text-sm font-medium flex items-center gap-2">
                  {{ t('chunkingStrategy') }}
                  <span v-if="chunkingStrategyLocked"
                    class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs
                           bg-warning/15 border border-warning/30 text-warning">
                    {{ t('importInProgressChangesAreLocked') }}
                  </span>
                </div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                  {{ t('chunkingHelp') }}
                  <span v-if="chunkingStrategyLocked" class="text-warning">
                    {{ t('settingsWereSavedToEnvWhenImport') }}
                  </span>
                </div>
              </div>
              <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <button type="button" :disabled="chunkingStrategyLocked"
                  @click="() => { form.CHUNKING_STRATEGY = 'fixed'; form.ADAPTIVE_CHUNK_MODEL_ENABLED = false }"
                  :class="['text-left px-3 py-2 rounded-lg border text-xs disabled:cursor-not-allowed',
                           form.CHUNKING_STRATEGY === 'fixed'
                             ? 'border-accent bg-accent/5 dark:border-night-accent dark:bg-night-accent/10'
                             : 'border-ink/10 dark:border-night-text/10 hover:border-ink/30']">
                  <div class="font-medium">{{ t('fixedWindowsDefault') }}</div>
                  <div class="text-ink-soft dark:text-night-text-soft mt-0.5">
                    {{ t('12MessagesPerWindowWith3Overlapping') }}
                  </div>
                </button>
                <button type="button" :disabled="chunkingStrategyLocked"
                  @click="() => { form.CHUNKING_STRATEGY = 'adaptive'; form.ADAPTIVE_CHUNK_MODEL_ENABLED = false }"
                  :class="['text-left px-3 py-2 rounded-lg border text-xs disabled:cursor-not-allowed',
                           form.CHUNKING_STRATEGY === 'adaptive' && !form.ADAPTIVE_CHUNK_MODEL_ENABLED
                             ? 'border-accent bg-accent/5 dark:border-night-accent dark:bg-night-accent/10'
                             : 'border-ink/10 dark:border-night-text/10 hover:border-ink/30']">
                  <div class="font-medium">{{ t('heuristicAdaptive') }}</div>
                  <div class="text-ink-soft dark:text-night-text-soft mt-0.5">
                    {{ t('usesCharacterBudgetTimeGapsAndTopic') }}
                  </div>
                </button>
                <button type="button" :disabled="chunkingStrategyLocked"
                  @click="() => { form.CHUNKING_STRATEGY = 'adaptive'; form.ADAPTIVE_CHUNK_MODEL_ENABLED = true }"
                  :class="['text-left px-3 py-2 rounded-lg border text-xs disabled:cursor-not-allowed',
                           form.CHUNKING_STRATEGY === 'adaptive' && form.ADAPTIVE_CHUNK_MODEL_ENABLED
                             ? 'border-accent bg-accent/5 dark:border-night-accent dark:bg-night-accent/10'
                             : 'border-ink/10 dark:border-night-text/10 hover:border-ink/30']">
                  <div class="font-medium">{{ t('modelAdaptiveMostPrecise') }}</div>
                  <div class="text-ink-soft dark:text-night-text-soft mt-0.5">
                    {{ t('aSmallModelFindsTopicBoundariesWith') }}
                  </div>
                </button>
              </div>

              <!-- 仅模型 adaptive 展开三件套配置；启发式 / fixed 不需要 -->
              <div v-if="form.CHUNKING_STRATEGY === 'adaptive' && form.ADAPTIVE_CHUNK_MODEL_ENABLED"
                class="ml-1 space-y-3 pt-2 border-t border-ink/5 dark:border-night-text/5">
                <div v-if="isChinese" class="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <button v-for="p in rerankerPresets" :key="p.id" type="button"
                    :disabled="chunkingStrategyLocked"
                    @click="applyAdaptiveChunkPreset(p)"
                    :class="['text-left px-3 py-2 rounded-lg border text-xs disabled:cursor-not-allowed',
                             (p.id === 'reuse-label' && form.ADAPTIVE_CHUNK_REUSE_LABEL)
                               || (p.id !== 'reuse-label' && !form.ADAPTIVE_CHUNK_REUSE_LABEL && form.ADAPTIVE_CHUNK_API_URL === p.base_url)
                               ? 'border-accent bg-accent/5 dark:border-night-accent dark:bg-night-accent/10'
                               : 'border-ink/10 dark:border-night-text/10 hover:border-ink/30']">
                    <div class="font-medium">{{ p.label }}</div>
                    <div class="text-ink-soft dark:text-night-text-soft mt-0.5">{{ p.hint }}</div>
                  </button>
                </div>
                <div v-if="!form.ADAPTIVE_CHUNK_REUSE_LABEL" class="space-y-3">
                  <label class="block">
                    <span class="text-sm">{{ t('apiURL') }}</span>
                    <input v-model="form.ADAPTIVE_CHUNK_API_URL" :disabled="chunkingStrategyLocked"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm
                             disabled:cursor-not-allowed" />
                  </label>
                  <label class="block">
                    <span class="text-sm">{{ t('apiKey') }}</span>
                    <input v-model="form.ADAPTIVE_CHUNK_API_KEY" type="password"
                      :disabled="chunkingStrategyLocked"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm
                             disabled:cursor-not-allowed" />
                  </label>
                  <label class="block">
                    <span class="text-sm">{{ t('modelName') }}</span>
                    <input v-model="form.ADAPTIVE_CHUNK_MODEL" :disabled="chunkingStrategyLocked"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm
                             disabled:cursor-not-allowed" />
                  </label>
                </div>

                <!-- 会话级并发：影响切分速度的关键。复用打标时留 0 跟随 LABEL_MAX_CONCURRENCY -->
                <label class="block">
                  <span class="text-sm flex items-center gap-2">
                    {{ t('sessionConcurrencyLimit') }}
                    <span v-if="form.ADAPTIVE_CHUNK_REUSE_LABEL && form.LABELING_ENABLED"
                      class="text-xs text-ink-soft dark:text-night-text-soft">
                      {{ t('0FollowsLabelingConcurrency') }}{{ form.LABEL_MAX_CONCURRENCY }}）
                    </span>
                    <span v-else
                      class="text-xs text-ink-soft dark:text-night-text-soft">
                      {{ t('0UsesTheDefaultOf4') }}
                    </span>
                  </span>
                  <input v-model.number="form.ADAPTIVE_CHUNK_MAX_CONCURRENCY"
                    type="number" min="0" max="50" step="1"
                    :disabled="chunkingStrategyLocked"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm
                           disabled:cursor-not-allowed" />
                  <span class="text-xs text-ink-soft dark:text-night-text-soft mt-1 block leading-relaxed">
                    {{ t('forExample34SessionsAt3Seconds') }}
                  </span>
                </label>
              </div>
            </div>

            <template v-if="!importTask">
              <!-- 中断恢复提示 -->
              <div v-if="resumeHint"
                class="rounded-lg px-3.5 py-2.5 text-xs leading-relaxed
                       bg-accent/5 dark:bg-night-accent/10
                       border border-accent/20 dark:border-night-accent/25
                       text-ink-soft dark:text-night-text-soft">
                {{ resumeHint }}
              </div>

              <!-- 已上传列表（来自第 1 步） -->
              <div v-if="uploadedFiles.length" class="space-y-2">
                <div v-for="f in uploadedFiles" :key="f.saved_as"
                  class="flex items-center gap-3 p-3 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/5 dark:border-night-text/10">
                  <FileText :size="18" class="text-ink-soft dark:text-night-text-soft" />
                  <div class="flex-1 min-w-0">
                    <div class="text-sm truncate">{{ f.name }}</div>
                    <div class="text-xs text-ink-soft dark:text-night-text-soft">
                      {{ (f.size / 1024 / 1024).toFixed(2) }} MB ·
                      {{ translateBackendText(f.format_label || f.format || t('unrecognized')) }}
                      · {{ f.total_messages || 0 }} {{ t('messages') }}
                    </div>
                  </div>
                </div>
                <p v-if="saveError" class="text-sm text-warning flex items-center gap-1.5">
                  <AlertCircle :size="14" /> {{ translateBackendText(saveError) }}
                </p>
                <button class="w-full py-2.5 rounded-full bg-accent text-paper-soft hover:bg-accent/90
                               disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
                  :disabled="importStarting || !!importTask"
                  @click="startImport">
                  <Loader2 v-if="importStarting" :size="14" class="animate-spin" />
                  <span>{{ importStarting ? t('starting') : t('startImport', { count: uploadedFiles.length }) }}</span>
                </button>
              </div>

              <!-- 空状态：第 1 步没有上传文件 -->
              <div v-else class="rounded-xl p-6 text-center bg-paper dark:bg-night-bg
                                 border border-dashed border-ink/15 dark:border-night-text/15">
                <FileText :size="22" class="mx-auto text-ink-soft dark:text-night-text-soft" />
                <p class="mt-2 text-sm">{{ t('noFilesToImport') }}</p>
                <p class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">
                  {{ t('goBackToStep1ToAdd') }}
                </p>
              </div>
            </template>

            <div v-else class="space-y-3">
              <!-- 耐心等待提示 -->
              <div v-if="!['done','failed','cancelled'].includes(importTask.status)"
                class="rounded-lg px-3.5 py-2.5 text-xs leading-relaxed
                       bg-accent/5 dark:bg-night-accent/10
                       border border-accent/20 dark:border-night-accent/25
                       text-ink-soft dark:text-night-text-soft">
                {{ t('largeHistoriesTakeTimeEach10000') }}
                <strong class="text-ink dark:text-night-text">{{ t('youCanMinimizeThisPageAndDo') }}</strong>{{ t('importContinuesInTheBackendAndProgress') }}
              </div>

              <div class="rounded-xl p-4 bg-paper dark:bg-night-bg border border-ink/10 dark:border-night-text/10">
                <div class="flex items-center gap-2 mb-1">
                  <Loader2 v-if="!['done','failed','cancelled'].includes(importTask.status)"
                    :size="16" class="animate-spin text-accent dark:text-night-accent" />
                  <CheckCircle2 v-else-if="importTask.status === 'done' && !importTask.error"
                    :size="16" class="text-accent dark:text-night-accent" />
                  <AlertCircle v-else :size="16" class="text-warning" />
                  <span class="text-sm font-medium">{{ importStatusTitle }}</span>
                  <span v-if="importStageElapsed"
                    class="ml-auto text-xs text-ink-soft dark:text-night-text-soft font-mono">
                    {{ importStageElapsed }}
                  </span>
                </div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft mb-2 pl-6">
                  {{ importStageMain || t('preparing') }}
                </div>
                <div class="h-1.5 rounded-full bg-ink/5 dark:bg-night-text/10 overflow-hidden">
                  <div class="h-full bg-accent dark:bg-night-accent transition-all duration-500"
                    :style="{ width: `${Math.round(importTask.progress * 100)}%` }" />
                </div>
                <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft text-right font-mono">
                  {{ Math.round(importTask.progress * 100) }}%
                </div>
                <p v-if="importTask.error" class="mt-2 text-sm text-warning">{{ translateBackendText(importTask.error) }}</p>
              </div>
              <button v-if="!['done','failed','cancelled'].includes(importTask.status)"
                class="text-xs text-ink-soft dark:text-night-text-soft hover:text-warning"
                @click="cancelImport">
                {{ t('cancelImportProcessedRecordsAreKept') }}
              </button>
              <!-- 失败/取消后允许用户重新开始（chunk_id 去重会自动跳过已入库部分） -->
              <button v-if="['failed', 'cancelled'].includes(importTask.status)"
                :disabled="importStarting"
                class="w-full py-2 rounded-full text-sm
                       border border-accent/40 text-accent dark:text-night-accent
                       hover:bg-accent/10 disabled:opacity-40"
                @click="startImport">
                <Loader2 v-if="importStarting" :size="14" class="animate-spin inline" />
                {{ importStarting ? t('starting') : t('restartImportRecordsAlreadyStoredAreSkipped') }}
              </button>
            </div>

          </div>

          <!-- 第 8 步：设置访问密码 -->
          <div v-else-if="step === 8" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">{{ t('setAccessPasswordXUWENAPIKEY') }}</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                {{ t('accessPasswordHelp') }}
              </p>
            </div>
            <label class="block">
              <span class="text-sm">XUWEN_API_KEY</span>
              <div class="mt-1 flex gap-2">
                <input v-model="form.XUWEN_API_KEY" type="text"
                  :placeholder="hasConfigured('XUWEN_API_KEY') ? t('alreadyConfiguredLeaveBlankToKeepIt') : t('useTheRefreshButtonToGenerateA')"
                  class="flex-1 px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                <button :title="t('generateRandomPassword')" @click="genApiKey"
                  class="px-3 py-2 rounded-lg border border-ink/10 dark:border-night-text/10
                         text-sm hover:bg-paper-shade dark:hover:bg-night-bg-soft">
                  <RefreshCw :size="14" />
                </button>
                <button :title="t('copy')" @click="copyApiKey"
                  class="px-3 py-2 rounded-lg border border-ink/10 dark:border-night-text/10
                         text-sm hover:bg-paper-shade dark:hover:bg-night-bg-soft">
                  <Copy :size="14" />
                </button>
              </div>
            </label>
            <p v-if="saveError" class="text-sm text-warning flex items-center gap-1.5">
              <AlertCircle :size="14" /> {{ translateBackendText(saveError) }}
            </p>
            <div class="rounded-lg p-3 bg-paper dark:bg-night-bg border border-ink/5 dark:border-night-text/10
                        text-xs text-ink-soft dark:text-night-text-soft leading-relaxed">
              {{ t('savingWritesChangesToBackendEnvAnd') }}
              <strong class="text-ink dark:text-night-text">{{ t('restartTheBackendToApplyConfigurationChanges') }}</strong>
            </div>
          </div>
        </section>

        <nav v-if="step > 0" class="mt-6 flex items-center justify-between gap-3">
          <button class="inline-flex items-center gap-1.5 px-4 py-2 rounded-full text-sm
                   text-ink-soft dark:text-night-text-soft hover:text-ink dark:hover:text-night-text
                   disabled:opacity-30"
            :disabled="step <= 1" @click="prev">
            <ArrowLeft :size="14" /> {{ t('previous') }}
          </button>
          <button v-if="step === 7 && (!importTask || ['failed', 'cancelled'].includes(importTask.status))"
            class="text-xs text-ink-soft dark:text-night-text-soft hover:text-ink dark:hover:text-night-text"
            @click="next">
            {{ t('skipForNowImportLater') }}
          </button>
          <button v-if="step < totalSteps"
            class="inline-flex items-center gap-1.5 px-5 py-2 rounded-full text-sm
                   bg-accent text-paper-soft hover:bg-accent/90
                   disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="!canNext" @click="next">
            {{ t('next') }} <ArrowRight :size="14" />
          </button>
          <button v-else
            class="inline-flex items-center gap-1.5 px-5 py-2 rounded-full text-sm
                   bg-accent text-paper-soft hover:bg-accent/90
                   disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="!hasValueOrConfigured('XUWEN_API_KEY') || saving"
            @click="finishWizard">
            <Loader2 v-if="saving" :size="14" class="animate-spin" />
            <span>{{ saving ? t('saving') : t('saveAndFinish') }}</span>
          </button>
        </nav>
      </template>
    </div>
  </div>
</template>
