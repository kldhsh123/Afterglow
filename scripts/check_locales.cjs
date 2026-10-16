// Run after installing frontend dependencies: node scripts/check_locales.cjs
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const root = path.resolve(__dirname, '..')
const frontendRequire = createRequire(path.join(root, 'frontend/package.json'))
const ts = frontendRequire('typescript')
const vue = frontendRequire('vue')
const { parse } = frontendRequire('vue/compiler-sfc')

function evaluate(file, globals = {}, dependency = frontendRequire) {
  const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, require: dependency, ...globals }, { filename: file })
  return module.exports
}
function checkPair(zh, en, prefix) {
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort(), `Locale keys differ at ${prefix}`)
  for (const key of Object.keys(zh)) {
    const name = `${prefix}.${key}`
    if (typeof zh[key] === 'string') {
      assert.equal(typeof en[key], 'string', name)
      assert.ok(en[key].trim(), `Empty English text: ${name}`)
      const parameters = value => [...value.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort()
      assert.deepEqual(parameters(en[key]), parameters(zh[key]), `Interpolation parameters differ: ${name}`)
    } else {
      assert.equal(Array.isArray(en[key]), Array.isArray(zh[key]), name)
      checkPair(zh[key], en[key], name)
    }
  }
}
function checkTemplates(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name)
    if (entry.isDirectory()) checkTemplates(file)
    else if (file.endsWith('.vue')) {
      const { descriptor } = parse(fs.readFileSync(file, 'utf8'))
      const visit = node => {
        if (node.type === 2) assert.doesNotMatch(node.content, /\p{Script=Han}/u, `Hardcoded UI text: ${file}:${node.loc.start.line}`)
        for (const prop of node.props || []) {
          if (prop.type === 6 && ['title', 'alt', 'placeholder', 'aria-label'].includes(prop.name)) {
            assert.doesNotMatch(prop.value?.content || '', /\p{Script=Han}/u, `Hardcoded attribute: ${file}`)
          }
        }
        for (const child of node.children || []) visit(child)
      }
      if (descriptor.template?.ast) visit(descriptor.template.ast)
    }
  }
}
for (const project of ['frontend', 'backend/web_ui_src']) {
  const src = path.join(root, project, 'src')
  const zh = evaluate(path.join(src, 'locales/zh-CN.ts')).default
  const en = evaluate(path.join(src, 'locales/en.ts')).default
  checkPair(zh, en, project)
  checkTemplates(src)
  const chat = project === 'frontend'
  const file = path.join(src, chat ? 'composables/useLocale.ts' : 'useLocale.ts')
  const storageKey = chat ? 'afterglow.locale' : 'afterglow.config.locale'
  function initialize(languages, stored, blocked = false) {
    const values = new Map(stored === undefined ? [] : [[storageKey, stored]])
    const localStorage = {
      getItem: key => { if (blocked) throw new Error('Storage disabled'); return values.get(key) ?? null },
      setItem: (key, value) => { if (blocked) throw new Error('Storage disabled'); values.set(key, value) },
    }
    const exported = evaluate(file, {
      ...(languages === undefined ? {} : { navigator: { languages, language: languages[0] } }), localStorage,
    }, name => name === 'vue' ? vue : name.endsWith('zh-CN') ? { default: zh } : { default: en })
    return { ...exported.useLocale(), setLocale: chat ? exported.setLocale : exported.useLocale().setLocale, values }
  }
  for (const language of ['zh-CN', 'zh-TW', 'zh-HK', 'zh-Hans', 'zh-Hant']) assert.equal(initialize([language]).locale.value, 'zh-CN', language)
  for (const language of ['en-US', 'fr-FR', 'ja-JP', 'de-DE']) assert.equal(initialize([language]).locale.value, 'en', language)
  assert.equal(initialize(['en-US', 'zh-TW']).locale.value, 'zh-CN')
  assert.equal(initialize(undefined).locale.value, 'en')
  assert.equal(initialize(['en'], 'invalid').locale.value, 'en')
  assert.equal(initialize(['zh-TW'], 'en').locale.value, 'en')
  assert.equal(initialize(['en'], 'zh-CN').locale.value, 'zh-CN')
  const active = initialize(['en'])
  const key = chat ? 'common.settings' : 'title'
  const before = active.t(key)
  active.setLocale('zh-CN')
  assert.equal(active.locale.value, 'zh-CN')
  assert.equal(active.values.get(storageKey), 'zh-CN')
  assert.notEqual(active.t(key), before)
  assert.equal(active.isChinese.value, true)
  active.setLocale('en')
  assert.equal(active.isChinese.value, false)
  assert.equal(active.t(chat ? 'chat.title' : 'startImport', chat ? { name: 'Alex' } : { count: 3 }), chat ? 'Conversation with Alex' : 'Start import (3 files)')
  active.setLocale('zh-CN')
  assert.equal(active.t('missing.translation.key'), '文案暂缺')
  active.setLocale('en')
  const noStorage = initialize(['en'], undefined, true)
  noStorage.setLocale('zh-CN')
  assert.equal(noStorage.locale.value, 'zh-CN')
  assert.equal(active.t('missing.translation.key'), active.locale.value === 'zh-CN' ? '文案暂缺' : 'Text unavailable')
  console.log(`${project}: catalogs, templates, detection, switching and persistence passed`)
}

const { applyEnglishDefaults } = evaluate(path.join(root, 'backend/web_ui_src/src/englishDefaults.ts'))
function emptyServices() {
  const form = { OPENAI_BASE_URL: '', CHAT_MODEL: '', EMBEDDING_API_URL: '', EMBEDDING_MODEL: '', EMBEDDING_DIM: 1024 }
  for (const prefix of ['LABEL', 'LIFE', 'RESPONSE_POLICY', 'VISION', 'ADAPTIVE_CHUNK']) {
    form[`${prefix}_API_URL`] = ''
    form[`${prefix}_MODEL`] = ''
  }
  return form
}
const fresh = emptyServices()
applyEnglishDefaults(fresh, new Set(), { base_url: 'https://generativelanguage.googleapis.com/v1beta/openai', default_model: 'preset-google-model' })
assert.equal(fresh.OPENAI_BASE_URL, 'https://generativelanguage.googleapis.com/v1beta/openai')
assert.equal(fresh.CHAT_MODEL, 'preset-google-model')
assert.equal(fresh.EMBEDDING_API_URL, 'https://api.openai.com/v1')
assert.equal(fresh.EMBEDDING_MODEL, 'text-embedding-3-small')
assert.equal(fresh.EMBEDDING_DIM, 1536)
assert.equal(fresh.LABEL_API_URL, 'https://api.openai.com/v1')
assert.equal(fresh.LABEL_MODEL, 'gpt-4.1-mini')
const before = { ...fresh }
applyEnglishDefaults(fresh, new Set())
assert.deepEqual(fresh, before, 'Repeated filling must preserve the selected model')
const existing = emptyServices()
existing.OPENAI_BASE_URL = 'https://my-provider.example/v2'
existing.EMBEDDING_MODEL = 'existing-model'
existing.EMBEDDING_DIM = 768
applyEnglishDefaults(existing, new Set(['LABEL_API_KEY', 'EMBEDDING_DIM']))
assert.equal(existing.OPENAI_BASE_URL, 'https://my-provider.example/v2')
assert.equal(existing.CHAT_MODEL, '', 'A partial provider configuration must remain intact')
assert.equal(existing.EMBEDDING_MODEL, 'existing-model')
assert.equal(existing.EMBEDDING_API_URL, '')
assert.equal(existing.EMBEDDING_DIM, 768)
assert.equal(existing.LABEL_API_URL, '', 'A saved secret must prevent replacing its provider')
const savedDimension = emptyServices()
applyEnglishDefaults(savedDimension, new Set(['EMBEDDING_DIM']))
assert.equal(savedDimension.EMBEDDING_DIM, 1024, 'Saved dimensions must remain intact')
console.log('English service defaults: official URLs, dimensions and existing configuration preservation passed')

const inherited = emptyServices()
Object.assign(inherited, {
  OPENAI_BASE_URL: 'https://api.openai.com/v1', CHAT_MODEL: 'gpt-4o-mini',
  EMBEDDING_API_URL: 'https://runtime.example/v1', EMBEDDING_MODEL: 'runtime-embedding',
  LABEL_MODEL: 'runtime-label',
})
const runtime = Object.fromEntries(Object.entries(inherited).map(([key, value]) => [key, String(value)]))
applyEnglishDefaults(inherited, new Set(), undefined, runtime)
assert.equal(inherited.OPENAI_BASE_URL, 'https://generativelanguage.googleapis.com/v1beta/openai')
assert.equal(inherited.EMBEDDING_MODEL, 'text-embedding-3-small')
assert.equal(inherited.EMBEDDING_DIM, 1536)
assert.equal(inherited.LABEL_MODEL, 'gpt-4.1-mini')
const typedKey = emptyServices()
typedKey.OPENAI_API_KEY = 'user-entered-key'
applyEnglishDefaults(typedKey, new Set())
assert.equal(typedKey.OPENAI_BASE_URL, '', 'An unsaved API key must protect its provider')
const edited = { ...emptyServices(), OPENAI_BASE_URL: 'https://edited.example/api' }
applyEnglishDefaults(edited, new Set(), undefined, runtime)
assert.equal(edited.OPENAI_BASE_URL, 'https://edited.example/api')
assert.equal(edited.CHAT_MODEL, '')
console.log('Inherited runtime defaults, manual edits and unsaved API key preservation passed')

const configSrc = path.join(root, 'backend/web_ui_src/src')
const zh = evaluate(path.join(configSrc, 'locales/zh-CN.ts')).default
const en = evaluate(path.join(configSrc, 'locales/en.ts')).default
let chinese = false
const interpolate = (value, params) => value.replace(/\{(\w+)\}/g, (_, key) => params[key])
const { translateBackendText } = evaluate(path.join(configSrc, 'useConfigMessages.ts'), {}, name => {
  if (name.endsWith('zh-CN')) return { default: zh }
  if (name.endsWith('/en')) return { default: en }
  return { useLocale: () => ({ isChinese: { get value() { return chinese } }, t: (key, params = {}) => interpolate((chinese ? zh : en)[key], params) }) }
}).useConfigMessages()
for (const key of Object.keys(zh).filter(key => key.startsWith('backend'))) {
  const params = Object.fromEntries([...zh[key].matchAll(/\{(\w+)\}/g)].map(match => [match[1], 'value.[]()+?中文']))
  const source = interpolate(zh[key], params)
  assert.equal(translateBackendText(source), interpolate(en[key], params), key)
}
assert.equal(translateBackendText('正在处理 记录.json（1/3） · 正在清洗与去重 42 条消息 · 已用时 0:12'),
  'Processing 记录.json (1/3) · Cleaning and deduplicating 42 messages · Elapsed 0:12')
assert.equal(translateBackendText('provider 自定义 error'), 'provider 自定义 error')
assert.equal(
  translateBackendText('未授权，请检查并输入后端启动时打印到控制台的访问token。'),
  'Unauthorized. Check the access token printed in the backend startup console and enter it here.',
)
chinese = true
assert.equal(translateBackendText('连接成功，模型 custom 可用'), '连接成功，模型 custom 可用')
assert.equal(translateBackendText('Connected successfully. Model custom is available.'), '连接成功，模型 custom 可用')
console.log('Backend results and import stages: dynamic parameters, language switching and unknown details passed')
