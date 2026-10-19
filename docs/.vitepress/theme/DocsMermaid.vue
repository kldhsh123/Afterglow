<script>
let sequence = Promise.resolve()
let nextId = 0
// Mermaid maintains shared rendering configuration. Serialize diagrams and theme updates.
function renderDiagram(code, dark) {
  const task = sequence.then(async () => {
    const {default: mermaid} = await import('mermaid')
    mermaid.initialize({startOnLoad:false, securityLevel:'strict', theme:dark ? 'dark' : 'default'})
    return (await mermaid.render(`afterglow-diagram-${++nextId}`,code)).svg
  })
  sequence = task.catch(() => {})
  return task
}
</script>
<script setup>
import {computed, onMounted, onBeforeUnmount, ref, watch} from 'vue'
import {useData} from 'vitepress'
import en from '../locales/en.mjs'
import zh from '../locales/zh-CN.mjs'
const props = defineProps({graph:{type:String,required:true},id:String})
const {isDark,lang} = useData()
const text = computed(() => lang.value.startsWith('zh') ? zh : en)
const svg = ref(''), failed = ref(false)
const expanded = ref(false), naturalWidth = ref(0)
let version = 0
let stop
async function render() {
  const current = ++version
  try {
    const result = await renderDiagram(decodeURIComponent(props.graph),isDark.value)
    if(current === version) {
      svg.value=result
      const viewBox = result.match(/viewBox="([^"]+)"/)
      naturalWidth.value = Number(viewBox?.[1].trim().split(/[\s,]+/)[2]) || 600
      failed.value=false
    }
  } catch {
    if(current === version) {svg.value='';failed.value=true}
  }
}
onMounted(() => {stop=watch([() => props.graph,isDark],render,{immediate:true})})
onBeforeUnmount(() => {version++;stop?.()})
</script>
<template>
  <div class="mermaid">
    <p v-if="failed" role="alert">{{text.diagramError}}</p>
    <template v-else>
      <button v-if="svg" type="button" :aria-expanded="expanded" @click="expanded = !expanded">{{expanded ? text.diagramFit : text.diagramExpand}}</button>
      <div class="diagram-scroll" tabindex="0" :aria-label="text.diagramRegion" role="region">
        <div :style="expanded ? {width: `${naturalWidth}px`} : undefined" v-html="svg" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.diagram-scroll { overflow-x: auto; }
button { color: var(--vp-c-brand-1); text-decoration: underline; margin-bottom: 12px; font-size: 14px; }
button:focus-visible, .diagram-scroll:focus-visible { outline: 2px solid var(--vp-c-brand-1); outline-offset: 2px; }
</style>
