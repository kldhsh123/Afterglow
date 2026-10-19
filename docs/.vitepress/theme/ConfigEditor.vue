<script>
import {reactive} from 'vue'
const draft = reactive({})
</script>
<script setup>
import {computed, ref} from 'vue'
import {useData, withBase} from 'vitepress'
import schema from '../../.site/env-schema.json'
import en from '../locales/en.mjs'
import zh from '../locales/zh-CN.mjs'
import {parseEnv, serializeEnv, validateValues} from './env-file.mjs'

const {lang} = useData()
const locale = computed(() => lang.value.startsWith('zh') ? 'zh' : 'en')
const t = computed(() => ({en,zh})[locale.value].configEditor)
// Keep a draft through in-site language navigation; never persist secrets to storage.

if (!Object.keys(draft).length) Object.assign(draft, Object.fromEntries(schema.map(f => [f.key,f.default])))
const scope = ref('basic'), query = ref(''), selectedGroup = ref(''), reveal = ref(false), preview = ref(false)
const message = ref(''), readError = ref(''), fileInput = ref(null)
const required = ['SELF_NAME','SELF_UID','FRIEND_NAME','FRIEND_UID','OPENAI_API_KEY','EMBEDDING_API_KEY','XUWEN_API_KEY']
const missing = computed(() => required.filter(key => !draft[key]?.trim()))
const errors = computed(() => validateValues(draft,schema))
const unknown = computed(() => Object.keys(draft).filter(key => !schema.some(f => f.key === key)))
const plain = value => value.replace(/\[([^\]]+)\]\([^)]+\)/g,'$1').replaceAll('`','')
const groups = computed(() => [...new Set(schema.map(f => f.text[locale.value].group))])
const visible = computed(() => schema.filter(f => (scope.value === 'all' || f.basic)
  && (!selectedGroup.value || f.text.en.group === selectedGroup.value)
  && `${f.key} ${f.text[locale.value].description}`.toLowerCase().includes(query.value.toLowerCase().trim())))
const grouped = computed(() => groups.value.map(name => ({name,fields:visible.value.filter(f => f.text[locale.value].group === name)})).filter(g => g.fields.length))
const output = computed(() => serializeEnv(draft,schema,locale.value,t.value.header))
function reset() {
  if (!window.confirm(t.value.resetPrompt)) return
  for (const key of Object.keys(draft)) delete draft[key]
  Object.assign(draft,Object.fromEntries(schema.map(f => [f.key,f.default])))
  message.value = ''; readError.value = ''; preview.value = false
}
async function load(event) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    if (file.size > 1024 * 1024) throw new Error('size')
    const decoded = new TextDecoder('utf-8', {fatal:true}).decode(await file.arrayBuffer())
    const parsed = parseEnv(decoded)
    // Replace only after parsing the whole file successfully.
    for (const key of Object.keys(draft)) delete draft[key]
    Object.assign(draft,Object.fromEntries(schema.map(f => [f.key,f.default])),parsed)
    // Normalize dotenv boolean synonyms accepted by pydantic.
    for (const f of schema.filter(f => f.type === 'bool')) {
      const raw = String(draft[f.key]).toLowerCase()
      if (['true','1','yes','on','y','t'].includes(raw)) draft[f.key] = 'true'
      else if (['false','0','no','off','n','f'].includes(raw)) draft[f.key] = 'false'
    }
    readError.value = ''; message.value = 'imported'; preview.value = false
  } catch (error) {
    readError.value = error.line ? `${t.value.parseError} ${error.line}` : t.value.fileError
    message.value = ''
  } finally { event.target.value = '' }
}
function download() {
  if (errors.value.length) return
  const url = URL.createObjectURL(new Blob([output.value],{type:'text/plain;charset=utf-8'}))
  const link = document.createElement('a'); link.href = url; link.download = '.env'; link.click()
  setTimeout(() => URL.revokeObjectURL(url),1000)
  message.value = 'downloaded'
}
</script>

<template>
  <section class="config-editor" :aria-label="t.guide">
    <p class="editor-local">{{t.local}}</p>
    <div class="editor-toolbar">
      <button type="button" @click="fileInput.click()">{{t.import}}</button>
      <button type="button" class="editor-primary" :disabled="errors.length > 0" @click="download">{{t.download}}</button>
      <button type="button" @click="reset">{{t.reset}}</button>
      <input ref="fileInput" type="file" accept=".env,.txt,.example,.zh-CN" hidden @change="load">
    </div>
    <p v-if="readError" role="alert" class="editor-error">{{readError}}</p>
    <p v-if="message" role="status">{{t[message]}}</p>
    <p v-if="unknown.length">{{t.unknown}} <code>{{unknown.join(', ')}}</code></p>
    <p v-if="missing.length" class="editor-note">{{t.incomplete}} <code>{{missing.join(', ')}}</code> {{t.draft}}</p>
    <p v-if="errors.length" role="alert" class="editor-error">{{t.invalid}} <code>{{errors.join(', ')}}</code></p>
    <div class="editor-filters">
      <label>{{t.search}}<input v-model="query" type="search"></label>
      <label>{{t.group}}<select v-model="selectedGroup" @change="scope = 'all'">
        <option value="">{{t.allGroups}}</option>
        <option v-for="group in groups" :key="group" :value="schema.find(f => f.text[locale].group === group).text.en.group">{{group}}</option>
      </select></label>
    </div>
    <div class="editor-options">
      <label><input v-model="scope" type="radio" value="basic"> {{t.basic}}</label>
      <label><input v-model="scope" type="radio" value="all"> {{t.all}}</label>
      <label><input v-model="reveal" type="checkbox"> {{t.showSecrets}}</label>
    </div>
    <p class="editor-count">{{t.count}}: {{visible.length}} / {{schema.length}}</p>
    <p v-if="!visible.length">{{t.noResults}}</p>
    <details v-for="group in grouped" :key="group.name" :open="scope === 'basic' || !!query || !!selectedGroup">
      <summary>{{group.name}} <span>{{group.fields.length}}</span></summary>
      <div v-for="field in group.fields" :key="field.key" class="editor-field">
        <label :for="`env-${field.key}`"><code>{{field.key}}</code><span v-if="required.includes(field.key)" class="editor-required">{{t.required}}</span></label>
        <p :id="`help-${field.key}`">{{plain(field.text[locale].description)}}</p>
        <select v-if="field.type === 'bool' || field.options" :id="`env-${field.key}`" v-model="draft[field.key]" :aria-describedby="`help-${field.key}`" :aria-invalid="errors.includes(field.key)">
          <option v-if="field.options && !field.options.includes(draft[field.key])" :value="draft[field.key]">{{draft[field.key]}}</option>
          <option v-for="option in field.type === 'bool' ? ['true','false'] : field.options" :key="option" :value="option">{{field.type === 'bool' ? t[option] : option}}</option>
        </select>
        <textarea v-else-if="!field.secret && (String(draft[field.key]).includes('\n') || field.key.endsWith('_PROMPT'))" :id="`env-${field.key}`" v-model="draft[field.key]" rows="3" :aria-describedby="`help-${field.key}`" :aria-invalid="errors.includes(field.key)" spellcheck="false" />
        <input v-else :id="`env-${field.key}`" v-model="draft[field.key]" :type="field.secret && !reveal ? 'password' : 'text'" :inputmode="['int','float'].includes(field.type) ? 'decimal' : undefined" autocomplete="off" spellcheck="false" :aria-describedby="`help-${field.key}`" :aria-invalid="errors.includes(field.key)">
        <small v-if="!field.secret">{{t.default}}: <code>{{field.default || t.empty}}</code></small>
      </div>
    </details>
    <p>{{t.validation}}</p>
    <p>{{t.interpolation}}</p>
    <a :href="withBase(`/${locale}/environment.html`)">{{t.reference}}</a>
    <div class="editor-toolbar"><button type="button" :aria-expanded="preview" @click="preview = !preview">{{preview ? t.hidePreview : t.preview}}</button><button type="button" class="editor-primary" :disabled="errors.length > 0" @click="download">{{t.download}}</button></div>
    <textarea v-if="preview" class="editor-preview" :value="output" readonly :aria-label="t.preview" spellcheck="false" />
  </section>
</template>

<style scoped>
.config-editor { margin: 24px 0; }
.editor-local { padding: 16px; border-left: 3px solid var(--vp-c-brand-1); background: var(--vp-c-bg-soft); }
.editor-toolbar, .editor-options { display:flex; flex-wrap:wrap; gap:12px; margin:20px 0; align-items:center; }
button { border:1px solid var(--vp-c-divider); padding:8px 14px; border-radius:6px; font:inherit; }
button:hover { border-color:var(--vp-c-brand-1); }
.editor-primary { color:var(--vp-button-brand-text); background:var(--vp-button-brand-bg); }
button:disabled { opacity:.5; cursor:not-allowed; }
.editor-filters { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.editor-filters label { display:flex; flex-direction:column; gap:6px; font-size:14px; }
input:not([type=checkbox]):not([type=radio]), select, textarea { width:100%; border:1px solid var(--vp-c-divider); border-radius:4px; padding:8px 10px; background:var(--vp-c-bg); color:var(--vp-c-text-1); font:inherit; }
input[type=checkbox], input[type=radio] { accent-color:var(--vp-c-brand-1); }
input:focus-visible, select:focus-visible, button:focus-visible, textarea:focus-visible, summary:focus-visible { outline:2px solid var(--vp-c-brand-1); outline-offset:2px; }
[aria-invalid=true] { border-color:var(--vp-c-danger-1) !important; }
.editor-options, .editor-count, .editor-note, small { font-size:13px; color:var(--vp-c-text-2); }
.editor-error { color:var(--vp-c-danger-1); overflow-wrap:anywhere; }
details { border-top:1px solid var(--vp-c-divider); padding:16px 0; }
summary { font-size:17px; font-weight:600; cursor:pointer; }
summary span { float:right; color:var(--vp-c-text-2); font-weight:400; }
.editor-field { padding:18px 0; }
.editor-field label { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.editor-field code, .editor-note code { overflow-wrap:anywhere; }
.editor-field p { font-size:14px; line-height:1.7; margin:8px 0; }
.editor-required { font-size:11px; color:var(--vp-c-text-2); }
small { display:block; margin-top:6px; overflow-wrap:anywhere; }
.editor-preview { min-height:360px; font-family:var(--vp-font-family-mono); font-size:12px; }
@media(max-width:640px) { .editor-filters { grid-template-columns:1fr; } .editor-toolbar button { flex:1 0 auto; } }
</style>
