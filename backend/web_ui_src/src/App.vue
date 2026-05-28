<script setup lang="ts">
// Afterglow 配置向导（后端自带，不依赖 frontend 项目）
// 6 步：token → 身份 → 关系 → 聊天 AI → 向量服务(含打标推荐) → 导入聊天记录 → 设置密码
import { computed, onMounted, reactive, ref, shallowRef } from 'vue'
import {
  ArrowLeft, ArrowRight, CheckCircle2, AlertCircle, Loader2,
  KeyRound, Copy, RefreshCw, ExternalLink, Upload, FileText, X,
  Wand2, UserCircle2,
} from 'lucide-vue-next'
import {
  api, getToken, setToken,
  type Preset, type TestResult, type ImportTaskState, type UploadedFile,
  type IdentityCandidate,
} from './api'

const step = ref(0)
const totalSteps = 8

// ---- token ----
const tokenInput = ref(getToken())
const tokenError = ref('')
const tokenChecking = ref(false)

// ---- form ----
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

const chatTest = ref<TestResult | null>(null)
const chatTesting = ref(false)
const embTest = ref<TestResult | null>(null)
const embTesting = ref(false)
const labelTest = ref<TestResult | null>(null)
const labelTesting = ref(false)

const uploadedFiles = ref<UploadedFile[]>([])
const importTask = ref<ImportTaskState | null>(null)
const importStarting = ref(false)
// 重启后端 / 后端任务被清空但前端还有 uploadedFiles 时，提示用户"可以续传"
const resumeHint = ref('')
let importSse: EventSource | null = null

// 用户在 step 1 选择的"信息量最大的文件"作为 persona 画像参考
// 默认指向消息数最多的那个上传文件；用户可改
const personaSourcePath = ref<string>('')

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

// step 1: 从聊天文件识别 UID
const inspecting = ref(false)
const inspectError = ref('')
const inspectCandidates = ref<IdentityCandidate[]>([])
const inspectFormat = ref<string>('')

const saving = ref(false)
const saveError = ref('')
const finished = ref(false)
const backupPath = ref<string | null>(null)

const relationships = [
  { value: 'friend', label: '朋友' },
  { value: 'lover', label: '恋人' },
  { value: 'family', label: '亲人' },
  { value: 'colleague', label: '同事' },
  { value: 'custom', label: '其他' },
]

const progress = computed(() => Math.round(((step.value) / (totalSteps)) * 100))

// ---- token check ----
async function checkToken() {
  if (!tokenInput.value.trim()) {
    tokenError.value = '请粘贴后端控制台打印的 setup token'
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
    const [presetsData] = await Promise.all([api.presets(), loadExistingValues()])
    chatPresets.value = presetsData.chat
    embPresets.value = presetsData.embedding
    labelPresets.value = presetsData.label
    rerankerPresets.value = presetsData.reranker
    crossRerankerPresets.value = presetsData.cross_reranker
    step.value = 1
    // token 校验成功后才尝试恢复未完成的导入任务，
    // 否则用旧 token 调 /import/* 会 401，且用户没机会更新 token。
    await tryResumeUnfinishedImport()
  } catch (e) {
    tokenError.value = (e as Error).message || '校验失败'
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
      if (!form.XUWEN_API_KEY) step.value = 8
    } else {
      step.value = 7
      subscribeTask(storedId)
    }
  } catch {
    // 任务在后端找不到（最常见原因：后端重启）。底层 chunk_id 去重支持续传。
    persistImportTask(null)
    if (uploadedFiles.value.length) {
      step.value = 7
      resumeHint.value = (
        '检测到上次有未完成的导入任务，后端可能已被重启。'
        + '已处理的聊天记录都保留在了向量库中，'
        + '点击"开始导入"会自动跳过已存部分继续未完成的工作。'
      )
    }
  }
}

async function loadExistingValues() {
  // 已写入的 .env 字段反向填回 form，让用户重开页面能接着填
  // secret 字段后端只回 mask，这里跳过（form 字段保持空，用户重新填）
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
    for (const [k, v] of Object.entries(data.values)) {
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
  } catch { /* first run, no .env yet */ }
}

async function next() {
  if (step.value >= totalSteps) return
  // 静默保存当前状态到 .env：用户随时关网页都不会丢已填字段。
  // saveConfig 内部会跳过空字符串字段，避免覆盖之前已填好的字段。
  // step 0（token 校验）不写 .env，跳过
  if (step.value > 0) await saveConfig({ silent: true })
  step.value += 1
}
function prev() { if (step.value > 0) step.value -= 1 }

// ---- presets ----
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

// ---- tests ----
async function testChat() {
  chatTesting.value = true; chatTest.value = null
  try {
    chatTest.value = await api.testChat(form.OPENAI_BASE_URL, form.OPENAI_API_KEY, form.CHAT_MODEL)
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

// ---- api key ----
async function genApiKey() {
  const data = await api.generateApiKey()
  form.XUWEN_API_KEY = data.token
}
function copyApiKey() {
  if (form.XUWEN_API_KEY && navigator.clipboard) {
    navigator.clipboard.writeText(form.XUWEN_API_KEY)
  }
}

// ---- step 1: 选文件 → 上传 + 嗅探 + 合并候选 ----
// 多文件场景：同时上传多份导出（QQ + 微信 / 多账号），后端逐个嗅探，
// 前端合并候选按 UID 去重；step 5 直接复用这些已上传文件，不再要求重传。
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
    // 累加到已上传列表（step 5 会直接用）
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

    // 自动挑信息量最大的文件作为 persona 参考（如果用户还没指定）
    if (!personaSourcePath.value && uploadedFiles.value.length) {
      const best = uploadedFiles.value
        .slice()
        .sort((a, b) => (b.total_messages || 0) - (a.total_messages || 0))[0]
      personaSourcePath.value = best.saved_as
    }
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

// ---- import flow ----
function removeUploaded(idx: number) {
  const removed = uploadedFiles.value.splice(idx, 1)[0]
  // 如果删的就是 persona 参考，重置默认
  if (removed && personaSourcePath.value === removed.saved_as) {
    const best = uploadedFiles.value
      .slice()
      .sort((a, b) => (b.total_messages || 0) - (a.total_messages || 0))[0]
    personaSourcePath.value = best ? best.saved_as : ''
  }
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
    const persona = personaSourcePath.value || null
    const res = await api.startImport(files, names, persona)
    persistImportTask(res.task_id)
    resumeHint.value = ''
    subscribeTask(res.task_id)
  } catch (e) {
    const msg = (e as Error).message || ''
    saveError.value = `启动导入失败：${msg}`
    // 已上传的文件路径可能因为后端重启 / 清理而失效，提示用户重新上传
    if (msg.includes('文件不存在') || msg.includes('not exist') || msg.includes('404')) {
      uploadedFiles.value = []
      persistUploadedFiles([])
      personaSourcePath.value = ''
      saveError.value = '已上传的文件在后端找不到（可能被清理或后端重启过）。请返回步骤 1 重新选择文件。'
    }
  } finally {
    importStarting.value = false
  }
}

function subscribeTask(taskId: string) {
  if (importSse) importSse.close()
  const es = new EventSource(api.taskStreamUrl(taskId))
  es.onmessage = (ev) => {
    try {
      importTask.value = JSON.parse(ev.data)
      if (importTask.value && ['done', 'failed', 'cancelled'].includes(importTask.value.status)) {
        es.close()
        // 任务结束清掉 localStorage 任务标记，但保留 uploadedFiles 列表
        persistImportTask(null)
      }
    } catch { /* ignore */ }
  }
  es.onerror = () => { es.close() }
  importSse = es
}

async function cancelImport() {
  if (importTask.value) await api.cancelTask(importTask.value.task_id)
}

// ---- save ----
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
      const errs = result.errors?.map(e => `${e.field}: ${e.message}`).join('；') || '校验失败'
      throw new Error(errs)
    }
    if (result.backup) backupPath.value = result.backup
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
    case 3: return !!chatTest.value?.ok
    case 4: return !!embTest.value?.ok && (!form.LABELING_ENABLED || !!labelTest.value?.ok)
    // step 5: 高级功能可选可跳过；开了视觉/联网搜索的话强制必填关键字段
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
  return chatPresets.value.find(p => p.id !== 'custom' && p.base_url === form.OPENAI_BASE_URL)?.apply_url
})

// 选中态判定：先匹配真实 preset，匹配不到（base_url 是用户自填的）则视为自定义中转站
const currentChatPresetId = computed(() => {
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
  return idx >= 0 ? s.slice(0, idx) : s
})
const importStageElapsed = computed(() => {
  const s = importTask.value?.stage || ''
  const idx = s.lastIndexOf(' · 已用时 ')
  return idx >= 0 ? s.slice(idx + 3) : ''   // "已用时 X:YY"
})
const importStatusTitle = computed(() => {
  const st = importTask.value?.status
  switch (st) {
    case 'done': return '导入完成'
    case 'failed': return '导入失败'
    case 'cancelled': return '已取消'
    case 'persona': return '生成人格画像中'
    case 'importing': return '正在向量化入库'
    default: return '准备中'
  }
})

onMounted(async () => {
  // 恢复持久化的上传列表
  const persisted = loadPersistedFiles()
  if (persisted.length) uploadedFiles.value = persisted

  // 有保存的 token → 自动校验 + 进入向导 + 恢复未完成的导入（在 checkToken 内部）
  // 无 token / token 失效时停在 step 0，等用户手动粘贴。
  if (tokenInput.value) await checkToken()
})
</script>

<template>
  <div class="paper-bg min-h-screen">
    <div class="max-w-2xl mx-auto px-4 py-8 sm:py-12">
      <!-- 完成页 -->
      <div v-if="finished" class="text-center py-12 space-y-5">
        <div class="inline-flex w-16 h-16 rounded-full bg-accent/15 text-accent
                    dark:bg-night-accent/15 dark:text-night-accent items-center justify-center">
          <CheckCircle2 :size="36" />
        </div>
        <h1 class="text-2xl font-medium">配置已保存</h1>
        <p class="text-sm text-ink-soft dark:text-night-text-soft max-w-md mx-auto leading-relaxed">
          所有更改已写入 backend/.env。请重启后端进程以应用新配置：<br />
          <code class="font-mono text-xs px-2 py-0.5 rounded bg-paper-shade dark:bg-night-bg-soft">
            Ctrl+C 后重新运行启动命令
          </code>
        </p>
        <p v-if="backupPath" class="text-xs text-ink-soft dark:text-night-text-soft">
          原 .env 已备份到 <code class="font-mono">{{ backupPath }}</code>
        </p>
        <p class="text-xs text-ink-soft dark:text-night-text-soft pt-4">
          现在您可以安全的关闭此页面。
        </p>
      </div>

      <template v-else>
        <header class="mb-8 text-center">
          <h1 class="text-2xl font-medium tracking-wide">Afterglow 配置向导</h1>
          <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
            一步步完成后即可开始与朋友对话。
          </p>
        </header>

        <!-- 进度条 -->
        <div v-if="step > 0" class="mb-8">
          <div class="flex items-center justify-between text-xs text-ink-soft dark:text-night-text-soft mb-2">
            <span>第 {{ step }} / {{ totalSteps }} 步</span>
            <span>{{ progress }}%</span>
          </div>
          <div class="h-1 rounded-full bg-ink/5 dark:bg-night-text/10 overflow-hidden">
            <div
              class="h-full bg-accent dark:bg-night-accent transition-all duration-500"
              :style="{ width: `${progress}%` }"
            />
          </div>
        </div>

        <section class="rounded-2xl bg-paper-soft dark:bg-night-bg-soft shadow-letter
                        border border-ink/5 dark:border-night-text/10 p-6 sm:p-8">

          <!-- step 0: token -->
          <div v-if="step === 0" class="space-y-5">
            <div class="flex items-center gap-2 text-accent dark:text-night-accent">
              <KeyRound :size="18" />
              <h2 class="text-lg font-medium">输入访问 token</h2>
            </div>
            <p class="text-sm leading-relaxed text-ink-soft dark:text-night-text-soft">
              首次配置需要从后端控制台获取一次性 token。后端启动后会在终端打印类似下面的内容：
            </p>
            <pre class="text-xs p-3 rounded-lg bg-paper dark:bg-night-bg
                        border border-ink/10 dark:border-night-text/10 overflow-x-auto"
            >配置 UI 已启用
路径：/config
访问 token（generated）：xxxxxxxxxxxx</pre>
            <label class="block">
              <span class="text-sm">把那一串 token 粘贴在这里</span>
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
              <AlertCircle :size="14" /> {{ tokenError }}
            </p>
            <button
              class="w-full py-2.5 rounded-full bg-accent text-paper-soft
                     hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed
                     inline-flex items-center justify-center gap-2"
              :disabled="tokenChecking || !tokenInput.trim()"
              @click="checkToken"
            >
              <Loader2 v-if="tokenChecking" :size="16" class="animate-spin" />
              <span>{{ tokenChecking ? '校验中…' : '继续' }}</span>
            </button>
          </div>

          <!-- step 1: 身份 -->
          <div v-else-if="step === 1" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">你和朋友是谁</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                这些信息用于识别聊天记录里"哪条是你说的、哪条是 ta 说的"。
              </p>
            </div>

            <!-- 从聊天文件识别 -->
            <div class="rounded-xl bg-paper dark:bg-night-bg
                        border border-accent/30 dark:border-night-accent/30 p-4 space-y-3">
              <div class="flex items-start gap-2">
                <Wand2 :size="16" class="mt-0.5 text-accent dark:text-night-accent flex-shrink-0" />
                <div class="flex-1">
                  <div class="text-sm font-medium">从聊天文件自动识别（推荐）</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    选一个或多个 QQ / 微信导出的 JSON，文件会直接上传至本机后端并解析双方身份。
                    跨平台或多账号场景可一次选多个文件，UID 自动累加为逗号列表。
                  </div>
                </div>
              </div>
              <label class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs
                            bg-accent text-paper-soft cursor-pointer hover:bg-accent/90
                            disabled:opacity-50">
                <input type="file" accept="application/json,.json" multiple class="hidden"
                  @change="onIdentifyFilesPicked" :disabled="inspecting" />
                <Loader2 v-if="inspecting" :size="12" class="animate-spin" />
                <Upload v-else :size="12" />
                <span>{{ inspecting ? '上传并解析中…' : '选择聊天文件' }}</span>
              </label>
              <p v-if="inspectError" class="text-xs text-warning flex items-center gap-1">
                <AlertCircle :size="12" /> {{ inspectError }}
              </p>

              <!-- 已上传文件 -->
              <div v-if="uploadedFiles.length" class="space-y-1.5 pt-1">
                <div class="text-xs text-ink-soft dark:text-night-text-soft">
                  已上传 {{ uploadedFiles.length }} 个文件，导入步骤会直接使用。
                  <span v-if="uploadedFiles.length > 1">
                    勾选下方"画像参考"指定哪份文件用来生成人格画像与作息分析
                    （默认选消息条数最多的一份）。
                  </span>
                </div>
                <div v-for="(f, i) in uploadedFiles" :key="f.saved_as"
                  class="flex items-center gap-2 px-2.5 py-1.5 rounded-md
                         bg-paper-soft dark:bg-night-bg-soft
                         border border-ink/5 dark:border-night-text/10 text-xs">
                  <FileText :size="14" class="text-ink-soft dark:text-night-text-soft flex-shrink-0" />
                  <span class="truncate flex-1">{{ f.name }}</span>
                  <span class="text-ink-soft dark:text-night-text-soft whitespace-nowrap">
                    {{ f.format === 'qqexporter_v5' ? 'QQ' : f.format === 'wechat_weflow' ? '微信' : '?' }}
                    · {{ f.total_messages || 0 }} 条
                  </span>
                  <button v-if="uploadedFiles.length > 1" type="button"
                    @click="personaSourcePath = f.saved_as"
                    class="px-2 py-0.5 rounded-full border text-xs whitespace-nowrap"
                    :class="personaSourcePath === f.saved_as
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'">
                    画像参考
                  </button>
                  <button type="button" @click="removeUploaded(i)"
                    class="p-0.5 text-ink-soft dark:text-night-text-soft hover:text-warning">
                    <X :size="12" />
                  </button>
                </div>
              </div>

              <!-- 候选列表 -->
              <div v-if="inspectCandidates.length" class="space-y-2 pt-1">
                <div class="text-xs text-ink-soft dark:text-night-text-soft leading-relaxed">
                  识别到 <b>{{ inspectCandidates.length }}</b> 个候选身份。
                  <b>跨平台账号</b>（同时导入 QQ 和微信、或多个小号）时，把对方的所有 UID 都点"设为朋友"，
                  你的所有 UID 都点"设为我"——支持多次累加。<b>未分配的 UID 在导入时会被当作陌生人忽略</b>。
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
                    设为我
                  </button>
                  <button type="button" @click="pickAsFriend(c)"
                    class="text-xs px-2.5 py-1 rounded-full border whitespace-nowrap"
                    :class="isPickedAsFriend(c)
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper-shade dark:hover:bg-night-bg-soft'">
                    设为朋友
                  </button>
                </div>
                <div v-if="unassignedCandidates.length > 0"
                  class="rounded-lg px-3.5 py-2.5 text-xs leading-relaxed
                         bg-warning/10 border border-warning/30 text-warning">
                  ⚠️ 还有 <b>{{ unassignedCandidates.length }}</b> 个候选 UID 未分配：
                  <span class="font-mono">{{ unassignedCandidates.map(c => c.uid).join('、') }}</span>。
                  如果是你或对方的另一个账号，记得点"设为我 / 设为朋友"。
                </div>
              </div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <label class="block">
                <span class="text-sm">你的昵称</span>
                <input v-model="form.SELF_NAME"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft" />
              </label>
              <label class="block">
                <span class="text-sm">你的账号 ID</span>
                <input v-model="form.SELF_UID" placeholder="QQ 为 u_xxx，微信为 wxid_xxx"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <label class="block">
                <span class="text-sm">朋友的昵称</span>
                <input v-model="form.FRIEND_NAME"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft" />
              </label>
              <label class="block">
                <span class="text-sm">朋友的账号 ID</span>
                <input v-model="form.FRIEND_UID" placeholder="多账号用逗号分隔"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
            </div>
            <p class="text-xs text-ink-soft dark:text-night-text-soft">
              如果不用上面的自动识别，可手动填写。QQ 见 chatInfo.selfUid，微信见 senders 数组的 wxid。
            </p>
          </div>

          <!-- step 2: 关系 -->
          <div v-else-if="step === 2" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">你们是什么关系</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">用于挑选合适的语气模板。</p>
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
              <span class="text-sm">填一句描述这段关系</span>
              <input v-model="form.RELATIONSHIP_DESCRIPTION"
                placeholder="例如：高中同桌、一起长大的发小"
                class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                       border border-ink/10 dark:border-night-text/10 outline-none
                       focus:ring-2 focus:ring-accent-soft" />
            </label>
          </div>

          <!-- step 3: 聊天 AI -->
          <div v-else-if="step === 3" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">选一个聊天 AI</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                负责最终的对话生成。挑一个服务商，填密钥后点测试。
              </p>
            </div>

            <div class="rounded-lg px-3.5 py-2.5 text-xs leading-relaxed
                        bg-accent/5 dark:bg-night-accent/10
                        border border-accent/20 dark:border-night-accent/25
                        text-ink-soft dark:text-night-text-soft">
              建议优先选择 DeepSeek、Gemini 等参数量较大、表达自然的模型。
              模型本身的表达力会直接决定最终对话的质感。
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
                <span class="text-sm">接口地址</span>
                <input v-model="form.OPENAI_BASE_URL"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <label class="block">
                <div class="flex items-baseline justify-between">
                  <span class="text-sm">密钥</span>
                  <a v-if="currentPresetApplyUrl" :href="currentPresetApplyUrl"
                    target="_blank" rel="noopener"
                    class="text-xs text-accent dark:text-night-accent hover:underline inline-flex items-center gap-1">
                    去申请密钥<ExternalLink :size="11" />
                  </a>
                </div>
                <input v-model="form.OPENAI_API_KEY" type="password"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <label class="block">
                <span class="text-sm">模型名</span>
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
                <span>{{ chatTesting ? '测试中…' : '测试连通' }}</span>
              </button>
              <div v-if="chatTest" class="text-sm flex items-center gap-1.5"
                :class="chatTest.ok ? 'text-accent dark:text-night-accent' : 'text-warning'">
                <CheckCircle2 v-if="chatTest.ok" :size="14" />
                <AlertCircle v-else :size="14" />
                <span>{{ chatTest.message }}</span>
              </div>
            </div>
          </div>

          <!-- step 4: 向量服务 + 打标 -->
          <div v-else-if="step === 4" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">选一个向量服务</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                用于把聊天记录转成向量。建议挑有免费额度的服务。
              </p>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
                <span class="text-sm">接口地址</span>
                <input v-model="form.EMBEDDING_API_URL"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <label class="block">
                <div class="flex items-baseline justify-between">
                  <span class="text-sm">密钥</span>
                  <a v-if="currentEmbPresetApplyUrl" :href="currentEmbPresetApplyUrl"
                    target="_blank" rel="noopener"
                    class="text-xs text-accent dark:text-night-accent hover:underline inline-flex items-center gap-1">
                    去申请密钥<ExternalLink :size="11" />
                  </a>
                </div>
                <input v-model="form.EMBEDDING_API_KEY" type="password"
                  class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
              </label>
              <div class="grid grid-cols-3 gap-3">
                <label class="block col-span-2">
                  <span class="text-sm">模型名</span>
                  <input v-model="form.EMBEDDING_MODEL"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">向量维度</span>
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
                <span>{{ embTesting ? '测试中…' : '测试连通' }}</span>
              </button>
              <div v-if="embTest" class="text-sm flex items-center gap-1.5"
                :class="embTest.ok ? 'text-accent dark:text-night-accent' : 'text-warning'">
                <CheckCircle2 v-if="embTest.ok" :size="14" />
                <AlertCircle v-else :size="14" />
                <span>{{ embTest.message }}</span>
              </div>
              <button v-if="embTest && !embTest.ok && embTest.extra?.actual_dim"
                class="text-xs px-3 py-1 rounded-full border border-accent/40 text-accent
                       dark:text-night-accent hover:bg-accent/10"
                @click="acceptActualDim">
                使用实际维度 {{ embTest.extra.actual_dim }}
              </button>
            </div>

            <!-- 打标推荐 -->
            <div class="mt-6 pt-5 border-t border-ink/5 dark:border-night-text/10 space-y-3">
              <label class="flex items-start gap-3 cursor-pointer">
                <input v-model="form.LABELING_ENABLED" type="checkbox"
                  class="mt-1 w-4 h-4 accent-accent dark:accent-night-accent" />
                <div>
                  <div class="text-sm font-medium">
                    启用语义打标
                    <span class="text-xs text-accent dark:text-night-accent ml-1">（推荐）</span>
                  </div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    让小模型给每条聊天打上情绪、话题、重要程度标签，显著提升 AI 找回忆的精准度。
                    首次导入会多花一些时间。建议用智谱 GLM-4-Flash（免费额度）。
                  </div>
                </div>
              </label>
              <div v-if="form.LABELING_ENABLED" class="ml-7 space-y-3">
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
                  <span class="text-sm">接口地址</span>
                  <input v-model="form.LABEL_API_URL"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <div class="flex items-baseline justify-between">
                    <span class="text-sm">密钥</span>
                    <a v-if="currentLabelPresetApplyUrl" :href="currentLabelPresetApplyUrl"
                      target="_blank" rel="noopener"
                      class="text-xs text-accent dark:text-night-accent hover:underline inline-flex items-center gap-1">
                      去申请密钥<ExternalLink :size="11" />
                    </a>
                  </div>
                  <input v-model="form.LABEL_API_KEY" type="password"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">模型名</span>
                  <input v-model="form.LABEL_MODEL"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">打标并发数</span>
                  <div class="mt-1 flex items-center gap-3">
                    <input v-model.number="form.LABEL_MAX_CONCURRENCY"
                      type="number" min="1" max="50"
                      class="w-24 px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm text-center" />
                    <span class="text-xs text-ink-soft dark:text-night-text-soft leading-snug">
                      智谱 GLM-4-Flash 免费账号上限 20，默认 19 留 1 余量。
                      其它服务按各自账号上限设置；遇到限流降一两档。
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
                    <span>{{ labelTesting ? '测试中…' : '测试打标服务' }}</span>
                  </button>
                  <div v-if="labelTest" class="text-xs flex items-center gap-1"
                    :class="labelTest.ok ? 'text-accent dark:text-night-accent' : 'text-warning'">
                    <CheckCircle2 v-if="labelTest.ok" :size="12" />
                    <AlertCircle v-else :size="12" />
                    <span>{{ labelTest.message }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- step 5: 可选功能 -->
          <div v-else-if="step === 5" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">可选功能</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                以下能力让对话更立体，可以全部跳过稍后再来。
              </p>
            </div>

            <!-- 重要：小功能模型选择策略提示 -->
            <div class="rounded-lg px-3.5 py-2.5 text-xs leading-relaxed
                        bg-accent/5 dark:bg-night-accent/10
                        border border-accent/20 dark:border-night-accent/25
                        text-ink-soft dark:text-night-text-soft">
              <span class="font-medium text-ink dark:text-night-text">关于速度的重要建议：</span>
              生活时间线、互动决策这些"幕后小功能"强烈建议复用打标模型（如 GLM-4-Flash）。
              如果让它们复用主聊天模型，每轮对话都会用大模型跑多次决策，
              返回速度会显著变慢。下面的卡片默认已经勾选了"复用打标模型"。
            </div>

            <!-- 生活时间线（必备，但可选复用方式） -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3">
              <div>
                <div class="text-sm font-medium">生活时间线</div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                  让 AI 拥有"作息感"：清晨醒来、工作、午休、晚上犯困……
                  会以小模型周期性维护当前状态。
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
                  复用打标模型（推荐）
                </button>
                <button type="button"
                  class="flex-1 py-2 rounded-lg border text-sm transition-colors"
                  :class="!form.LIFE_REUSE_LABEL || !form.LABELING_ENABLED
                    ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                    : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                  @click="form.LIFE_REUSE_LABEL = false">
                  自定义模型
                </button>
              </div>
              <div v-if="form.LIFE_REUSE_LABEL && form.LABELING_ENABLED"
                class="text-xs text-ink-soft dark:text-night-text-soft px-1">
                将使用 <code class="font-mono">{{ form.LABEL_MODEL || '打标模型' }}</code>
                （来自上一步的打标配置）。
              </div>
              <div v-else-if="form.LIFE_REUSE_LABEL && !form.LABELING_ENABLED"
                class="text-xs text-warning px-1">
                你尚未启用打标，无法复用。请返回上一步启用，或下方切换为"自定义"。
              </div>
              <div v-if="!form.LIFE_REUSE_LABEL || !form.LABELING_ENABLED" class="space-y-3">
                <div class="text-xs text-ink-soft dark:text-night-text-soft px-1">
                  全部留空时会 fallback 到主聊天模型（不推荐，慢）。
                </div>
                <label class="block">
                  <span class="text-sm">接口地址</span>
                  <input v-model="form.LIFE_API_URL" placeholder="例如 https://open.bigmodel.cn/api/paas/v4"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">密钥</span>
                  <input v-model="form.LIFE_API_KEY" type="password"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">模型名</span>
                  <input v-model="form.LIFE_MODEL" placeholder="例如 glm-4-flash"
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
                  <div class="text-sm font-medium">互动决策小模型</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    规则引擎会先判断本轮该用哪种语气（撒娇/接梗/认真/沉默…），
                    开启此项会让小模型在规则之上做有界微调，让回复意图更贴合上下文。
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
                    复用打标模型（推荐）
                  </button>
                  <button type="button"
                    class="flex-1 py-2 rounded-lg border text-sm transition-colors"
                    :class="!form.RESPONSE_POLICY_REUSE_LABEL || !form.LABELING_ENABLED
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'"
                    @click="form.RESPONSE_POLICY_REUSE_LABEL = false">
                    自定义模型
                  </button>
                </div>
                <div v-if="form.RESPONSE_POLICY_REUSE_LABEL && form.LABELING_ENABLED"
                  class="text-xs text-ink-soft dark:text-night-text-soft px-1">
                  将使用 <code class="font-mono">{{ form.LABEL_MODEL || '打标模型' }}</code>。
                </div>
                <div v-if="!form.RESPONSE_POLICY_REUSE_LABEL || !form.LABELING_ENABLED" class="space-y-3">
                  <label class="block">
                    <span class="text-sm">接口地址</span>
                    <input v-model="form.RESPONSE_POLICY_API_URL"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                  <label class="block">
                    <span class="text-sm">密钥</span>
                    <input v-model="form.RESPONSE_POLICY_API_KEY" type="password"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                  <label class="block">
                    <span class="text-sm">模型名</span>
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
                  <div class="text-sm font-medium">视觉理解</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    开启后可以向 AI 发图片。若主聊天 AI 已是多模态模型（如 GPT-4o / Gemini / Qwen-VL），
                    勾选下面的"主模型支持视觉"即可；否则需要单独配一个视觉模型。
                  </div>
                </div>
              </label>
              <div v-if="form.VISION_ENABLED" class="ml-7 space-y-3">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input v-model="form.CHAT_MODEL_SUPPORTS_VISION" type="checkbox"
                    class="w-4 h-4 accent-accent dark:accent-night-accent" />
                  <span class="text-sm">主聊天 AI 已支持视觉（如 GPT-4o / Gemini / Qwen-VL）</span>
                </label>
                <div v-if="!form.CHAT_MODEL_SUPPORTS_VISION" class="space-y-3">
                  <label class="block">
                    <span class="text-sm">视觉模型接口地址</span>
                    <input v-model="form.VISION_API_URL"
                      placeholder="例如 https://dashscope.aliyuncs.com/compatible-mode/v1"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                  <label class="block">
                    <span class="text-sm">密钥</span>
                    <input v-model="form.VISION_API_KEY" type="password"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                  </label>
                  <label class="block">
                    <span class="text-sm">模型名</span>
                    <input v-model="form.VISION_MODEL" placeholder="例如 qwen-vl-plus"
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
                  <div class="text-sm font-medium">联网搜索</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    用户明确要求"查一下/搜索/最新"时，后端会先去搜索引擎拿摘要再交给 AI。
                    会把本轮查询发给搜索服务，普通聊天不联网。
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
                    Tavily（月度免费额度）
                  </button>
                  <button type="button" @click="() => { form.WEB_SEARCH_PROVIDER = 'searxng'; form.WEB_SEARCH_BASE_URL = '' }"
                    class="flex-1 py-2 rounded-lg border text-sm transition-colors"
                    :class="form.WEB_SEARCH_PROVIDER === 'searxng'
                      ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                      : 'border-ink/10 dark:border-night-text/10 hover:bg-paper dark:hover:bg-night-bg'">
                    自建 SearXNG
                  </button>
                </div>
                <label class="block">
                  <div class="flex items-baseline justify-between">
                    <span class="text-sm">接口地址</span>
                    <a v-if="form.WEB_SEARCH_PROVIDER === 'tavily'"
                      href="https://app.tavily.com/" target="_blank" rel="noopener"
                      class="text-xs text-accent dark:text-night-accent hover:underline inline-flex items-center gap-1">
                      去申请密钥<ExternalLink :size="11" />
                    </a>
                  </div>
                  <input v-model="form.WEB_SEARCH_BASE_URL"
                    :placeholder="form.WEB_SEARCH_PROVIDER === 'tavily' ? 'https://api.tavily.com' : '你的 SearXNG 实例地址'"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label v-if="form.WEB_SEARCH_PROVIDER === 'tavily'" class="block">
                  <span class="text-sm">Tavily 密钥</span>
                  <input v-model="form.WEB_SEARCH_API_KEY" type="password" placeholder="tvly-..."
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="flex items-center gap-2 cursor-pointer">
                  <input v-model="form.WEB_FETCH_ENABLED" type="checkbox"
                    class="w-4 h-4 accent-accent dark:accent-night-accent" />
                  <span class="text-sm">用户消息含链接时，自动读取网页正文</span>
                </label>
              </div>
            </div>
          </div>

          <!-- step 6: 检索增强（可选） -->
          <div v-else-if="step === 6" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">检索增强（可选）</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft leading-relaxed">
                让 AI 在调聊天模型之前，先精准找到"相关的历史聊天片段"做参考。两项都可单独开关，
                也都可以跳过这一步（用默认 RRF 检索）。配置不齐时后端会自动降级，不会让聊天炸掉。
              </p>
            </div>

            <!-- query rewrite -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3">
              <label class="flex items-start gap-3 cursor-pointer">
                <input v-model="form.QUERY_REWRITE_ENABLED" type="checkbox"
                  class="mt-1 w-4 h-4 accent-accent dark:accent-night-accent" />
                <div class="flex-1">
                  <div class="text-sm font-medium">Query 改写</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    用户输入"在吗 / 想你了 / 好累"这类短句时，让小模型先改写成 1-3 个检索友好的查询。
                    模型直接复用打标 / 互动决策 / 生活时间线模型，无需额外配置。
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
                  <div class="text-sm font-medium">Cross-encoder 粗排（更精准的召回）</div>
                  <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                    专用 reranker 模型，在 LLM 精排之前先按"相关性"砍掉一半噪声候选。
                    跟上面的 LLM 精排是<b>互补</b>不是替换：两个都开 = 两阶段质量最高。
                  </div>
                </div>
              </label>
              <div v-if="form.CROSS_RERANK_ENABLED" class="ml-7 space-y-3">
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
                  当前协议：<span class="font-mono">{{ form.CROSS_RERANK_PROTOCOL }}</span>
                  <span v-if="form.CROSS_RERANK_PROTOCOL === 'dashscope'">（阿里 DashScope text-rerank 原生 API）</span>
                  <span v-else>（Jina / Cohere / SiliconFlow / 自建 bge-reranker 兼容协议）</span>
                </div>

                <label class="block">
                  <span class="text-sm">接口地址</span>
                  <input v-model="form.CROSS_RERANK_API_URL"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">密钥</span>
                  <input v-model="form.CROSS_RERANK_API_KEY" type="password"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
                <label class="block">
                  <span class="text-sm">模型名</span>
                  <input v-model="form.CROSS_RERANK_MODEL"
                    placeholder="例如 gte-rerank-v2 / BAAI/bge-reranker-v2-m3"
                    class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                           border border-ink/10 dark:border-night-text/10 outline-none
                           focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                </label>
              </div>
            </div>
          </div>

          <!-- step 7: 导入 -->
          <div v-else-if="step === 7" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">导入聊天记录</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                确认下方文件无误后开始导入。如果暂时不想导入也可以跳过，稍后从设置页再来。
                若需要增减文件，请返回步骤 1 重新识别身份。
              </p>
            </div>

            <!-- 切分策略（影响下一次导入；对已入库 chunks 无效） -->
            <div class="rounded-xl border border-ink/10 dark:border-night-text/10 p-4 space-y-3"
              :class="{ 'opacity-60 pointer-events-none': chunkingStrategyLocked }">
              <div>
                <div class="text-sm font-medium flex items-center gap-2">
                  切分策略
                  <span v-if="chunkingStrategyLocked"
                    class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs
                           bg-warning/15 border border-warning/30 text-warning">
                    🔒 导入进行中，无法修改
                  </span>
                </div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft mt-0.5 leading-relaxed">
                  决定聊天记录怎么切成片段入库。仅影响<b>下一次导入</b>，对已入库的 chunks 无效。
                  <span v-if="chunkingStrategyLocked" class="text-warning">
                    导入开始时设置已写入 .env，更改对本次任务不会生效；等待导入结束后可重新调整。
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
                  <div class="font-medium">固定窗口（默认）</div>
                  <div class="text-ink-soft dark:text-night-text-soft mt-0.5">
                    每 12 条消息一窗，3 条重叠。简单稳定。
                  </div>
                </button>
                <button type="button" :disabled="chunkingStrategyLocked"
                  @click="() => { form.CHUNKING_STRATEGY = 'adaptive'; form.ADAPTIVE_CHUNK_MODEL_ENABLED = false }"
                  :class="['text-left px-3 py-2 rounded-lg border text-xs disabled:cursor-not-allowed',
                           form.CHUNKING_STRATEGY === 'adaptive' && !form.ADAPTIVE_CHUNK_MODEL_ENABLED
                             ? 'border-accent bg-accent/5 dark:border-night-accent dark:bg-night-accent/10'
                             : 'border-ink/10 dark:border-night-text/10 hover:border-ink/30']">
                  <div class="font-medium">启发式 adaptive</div>
                  <div class="text-ink-soft dark:text-night-text-soft mt-0.5">
                    按字符预算 + 时间间隔 + 话题转折词切分。不调模型，免费。
                  </div>
                </button>
                <button type="button" :disabled="chunkingStrategyLocked"
                  @click="() => { form.CHUNKING_STRATEGY = 'adaptive'; form.ADAPTIVE_CHUNK_MODEL_ENABLED = true }"
                  :class="['text-left px-3 py-2 rounded-lg border text-xs disabled:cursor-not-allowed',
                           form.CHUNKING_STRATEGY === 'adaptive' && form.ADAPTIVE_CHUNK_MODEL_ENABLED
                             ? 'border-accent bg-accent/5 dark:border-night-accent dark:bg-night-accent/10'
                             : 'border-ink/10 dark:border-night-text/10 hover:border-ink/30']">
                  <div class="font-medium">模型 adaptive（最准）</div>
                  <div class="text-ink-soft dark:text-night-text-soft mt-0.5">
                    小模型返回话题边界，启发式做兜底。导入会多花一些 token。
                  </div>
                </button>
              </div>

              <!-- 仅模型 adaptive 展开三件套配置；启发式 / fixed 不需要 -->
              <div v-if="form.CHUNKING_STRATEGY === 'adaptive' && form.ADAPTIVE_CHUNK_MODEL_ENABLED"
                class="ml-1 space-y-3 pt-2 border-t border-ink/5 dark:border-night-text/5">
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
                    <span class="text-sm">接口地址</span>
                    <input v-model="form.ADAPTIVE_CHUNK_API_URL" :disabled="chunkingStrategyLocked"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm
                             disabled:cursor-not-allowed" />
                  </label>
                  <label class="block">
                    <span class="text-sm">密钥</span>
                    <input v-model="form.ADAPTIVE_CHUNK_API_KEY" type="password"
                      :disabled="chunkingStrategyLocked"
                      class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                             border border-ink/10 dark:border-night-text/10 outline-none
                             focus:ring-2 focus:ring-accent-soft font-mono text-sm
                             disabled:cursor-not-allowed" />
                  </label>
                  <label class="block">
                    <span class="text-sm">模型名</span>
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
                    会话级并发上限
                    <span v-if="form.ADAPTIVE_CHUNK_REUSE_LABEL && form.LABELING_ENABLED"
                      class="text-xs text-ink-soft dark:text-night-text-soft">
                      （留 0 跟随打标并发：{{ form.LABEL_MAX_CONCURRENCY }}）
                    </span>
                    <span v-else
                      class="text-xs text-ink-soft dark:text-night-text-soft">
                      （留 0 用默认 4）
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
                    34 个会话 × 串行 3 秒/次 ≈ 100 秒；调到 10 并发 ≈ 10 秒。
                    超过上游服务的并发额度会触发 429，按账号实际上限调（GLM-4-Flash 免费账号 ~19）。
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

              <!-- 已上传列表（来自 step 1） -->
              <div v-if="uploadedFiles.length" class="space-y-2">
                <div v-for="f in uploadedFiles" :key="f.saved_as"
                  class="flex items-center gap-3 p-3 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/5 dark:border-night-text/10">
                  <FileText :size="18" class="text-ink-soft dark:text-night-text-soft" />
                  <div class="flex-1 min-w-0">
                    <div class="text-sm truncate">{{ f.name }}</div>
                    <div class="text-xs text-ink-soft dark:text-night-text-soft">
                      {{ (f.size / 1024 / 1024).toFixed(2) }} MB ·
                      {{ f.format === 'qqexporter_v5' ? 'QQ' :
                         f.format === 'wechat_weflow' ? '微信' : '未识别' }}
                      · {{ f.total_messages || 0 }} 条消息
                    </div>
                  </div>
                </div>
                <p v-if="saveError" class="text-sm text-warning flex items-center gap-1.5">
                  <AlertCircle :size="14" /> {{ saveError }}
                </p>
                <button class="w-full py-2.5 rounded-full bg-accent text-paper-soft hover:bg-accent/90
                               disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
                  :disabled="importStarting || !!importTask"
                  @click="startImport">
                  <Loader2 v-if="importStarting" :size="14" class="animate-spin" />
                  <span>{{ importStarting ? '启动中…' : `开始导入（${uploadedFiles.length} 个文件）` }}</span>
                </button>
              </div>

              <!-- 空状态：step 1 没上传文件 -->
              <div v-else class="rounded-xl p-6 text-center bg-paper dark:bg-night-bg
                                 border border-dashed border-ink/15 dark:border-night-text/15">
                <FileText :size="22" class="mx-auto text-ink-soft dark:text-night-text-soft" />
                <p class="mt-2 text-sm">没有要导入的文件</p>
                <p class="mt-1 text-xs text-ink-soft dark:text-night-text-soft">
                  返回步骤 1 添加聊天文件，或直接跳过这一步
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
                较多的聊天记录会让处理变得比较漫长，请耐心等待。
                每万条消息一般需要数分钟到十几分钟，取决于服务商响应速度和你的网络。
                <strong class="text-ink dark:text-night-text">可以最小化网页继续做别的事</strong>，
                后端会持续在后台跑；下次回来还能看到进度。
              </div>

              <div class="rounded-xl p-4 bg-paper dark:bg-night-bg border border-ink/10 dark:border-night-text/10">
                <div class="flex items-center gap-2 mb-1">
                  <Loader2 v-if="!['done','failed','cancelled'].includes(importTask.status)"
                    :size="16" class="animate-spin text-accent dark:text-night-accent" />
                  <CheckCircle2 v-else-if="importTask.status === 'done'"
                    :size="16" class="text-accent dark:text-night-accent" />
                  <AlertCircle v-else :size="16" class="text-warning" />
                  <span class="text-sm font-medium">{{ importStatusTitle }}</span>
                  <span v-if="importStageElapsed"
                    class="ml-auto text-xs text-ink-soft dark:text-night-text-soft font-mono">
                    {{ importStageElapsed }}
                  </span>
                </div>
                <div class="text-xs text-ink-soft dark:text-night-text-soft mb-2 pl-6">
                  {{ importStageMain || '准备中…' }}
                </div>
                <div class="h-1.5 rounded-full bg-ink/5 dark:bg-night-text/10 overflow-hidden">
                  <div class="h-full bg-accent dark:bg-night-accent transition-all duration-500"
                    :style="{ width: `${Math.round(importTask.progress * 100)}%` }" />
                </div>
                <div class="mt-1 text-xs text-ink-soft dark:text-night-text-soft text-right font-mono">
                  {{ Math.round(importTask.progress * 100) }}%
                </div>
                <p v-if="importTask.error" class="mt-2 text-sm text-warning">{{ importTask.error }}</p>
              </div>
              <button v-if="!['done','failed','cancelled'].includes(importTask.status)"
                class="text-xs text-ink-soft dark:text-night-text-soft hover:text-warning"
                @click="cancelImport">
                取消导入（已处理的记录会保留）
              </button>
              <!-- 失败/取消后允许用户重新开始（chunk_id 去重会自动跳过已入库部分） -->
              <button v-if="['failed', 'cancelled'].includes(importTask.status)"
                :disabled="importStarting"
                class="w-full py-2 rounded-full text-sm
                       border border-accent/40 text-accent dark:text-night-accent
                       hover:bg-accent/10 disabled:opacity-40"
                @click="startImport">
                <Loader2 v-if="importStarting" :size="14" class="animate-spin inline" />
                {{ importStarting ? '启动中…' : '重新开始导入（自动跳过已入库部分）' }}
              </button>
            </div>
          </div>

          <!-- step 8: 设置访问密码 -->
          <div v-else-if="step === 8" class="space-y-5">
            <div>
              <h2 class="text-lg font-medium">设置访问密码（XUWEN_API_KEY）</h2>
              <p class="mt-1 text-sm text-ink-soft dark:text-night-text-soft">
                这是<strong class="text-ink dark:text-night-text">本地后端服务的访问密码</strong>，
                对应 <code class="font-mono text-xs">.env</code> 里的
                <code class="font-mono text-xs">XUWEN_API_KEY</code> 字段。
                聊天前端、OpenAI 兼容客户端访问后端 API 时都要带这个密码做 Bearer token。
                建议直接生成长随机串，避免别人借用你的 AI 额度。
              </p>
            </div>
            <label class="block">
              <span class="text-sm">XUWEN_API_KEY</span>
              <div class="mt-1 flex gap-2">
                <input v-model="form.XUWEN_API_KEY" type="text"
                  placeholder="点右侧刷新按钮生成随机密码"
                  class="flex-1 px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                         border border-ink/10 dark:border-night-text/10 outline-none
                         focus:ring-2 focus:ring-accent-soft font-mono text-sm" />
                <button title="生成随机密码" @click="genApiKey"
                  class="px-3 py-2 rounded-lg border border-ink/10 dark:border-night-text/10
                         text-sm hover:bg-paper-shade dark:hover:bg-night-bg-soft">
                  <RefreshCw :size="14" />
                </button>
                <button title="复制" @click="copyApiKey"
                  class="px-3 py-2 rounded-lg border border-ink/10 dark:border-night-text/10
                         text-sm hover:bg-paper-shade dark:hover:bg-night-bg-soft">
                  <Copy :size="14" />
                </button>
              </div>
            </label>
            <p v-if="saveError" class="text-sm text-warning flex items-center gap-1.5">
              <AlertCircle :size="14" /> {{ saveError }}
            </p>
            <div class="rounded-lg p-3 bg-paper dark:bg-night-bg border border-ink/5 dark:border-night-text/10
                        text-xs text-ink-soft dark:text-night-text-soft leading-relaxed">
              保存后会写入 backend/.env，原文件自动备份。
              <strong class="text-ink dark:text-night-text">配置变更需要重启后端进程才会生效</strong>。
            </div>
          </div>
        </section>

        <nav v-if="step > 0" class="mt-6 flex items-center justify-between gap-3">
          <button class="inline-flex items-center gap-1.5 px-4 py-2 rounded-full text-sm
                   text-ink-soft dark:text-night-text-soft hover:text-ink dark:hover:text-night-text
                   disabled:opacity-30"
            :disabled="step <= 1" @click="prev">
            <ArrowLeft :size="14" /> 上一步
          </button>
          <button v-if="step === 7 && (!importTask || ['failed', 'cancelled'].includes(importTask.status))"
            class="text-xs text-ink-soft dark:text-night-text-soft hover:text-ink dark:hover:text-night-text"
            @click="next">
            暂时跳过，稍后导入
          </button>
          <button v-if="step < totalSteps"
            class="inline-flex items-center gap-1.5 px-5 py-2 rounded-full text-sm
                   bg-accent text-paper-soft hover:bg-accent/90
                   disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="!canNext" @click="next">
            下一步 <ArrowRight :size="14" />
          </button>
          <button v-else
            class="inline-flex items-center gap-1.5 px-5 py-2 rounded-full text-sm
                   bg-accent text-paper-soft hover:bg-accent/90
                   disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="!form.XUWEN_API_KEY || saving"
            @click="finishWizard">
            <Loader2 v-if="saving" :size="14" class="animate-spin" />
            <span>{{ saving ? '保存中…' : '保存并完成' }}</span>
          </button>
        </nav>
      </template>
    </div>
  </div>
</template>
