<script setup lang="ts">
// 输入框：Enter 发送、Shift+Enter 换行、自动高度
// 支持图片：粘贴截图（Ctrl+V）/ 拖拽 / 点击上传
// 支持文档：txt/md/pdf/docx/xlsx/csv/html 等，上传后提取文本拼接到输入框
// 支持表情包：点击表情按钮从我的表情库选一张插入到消息
import { computed, nextTick, ref } from 'vue'
import { FileText, ImagePlus, Send, StickyNote, StopCircle, X } from 'lucide-vue-next'
import { useChatStore } from '@/stores/chat'
import { extractImagesFromClipboard, fileToDataUrl } from '@/composables/imageUpload'
import { extractDocument, listStickers, type Sticker } from '@/api/extra'

const emit = defineEmits<{
  (e: 'send', text: string, images: string[]): void
  (e: 'stop'): void
}>()

const chat = useChatStore()
const text = ref('')
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const imageInputRef = ref<HTMLInputElement | null>(null)
const docInputRef = ref<HTMLInputElement | null>(null)
const pendingImages = ref<string[]>([])
const isDragging = ref(false)
const docUploading = ref(false)

const myStickers = ref<Sticker[]>([])
const stickersOpen = ref(false)
let stickersLoaded = false

async function loadStickers() {
  try {
    const r = await listStickers('self')
    myStickers.value = r.items
    stickersLoaded = true
  } catch {
    myStickers.value = []
  }
}

const canSend = computed(
  () => text.value.trim().length > 0 || pendingImages.value.length > 0,
)

async function autoResize() {
  await nextTick()
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    submit()
  }
}

async function handleImageFiles(files: File[] | FileList | null) {
  if (!files) return
  for (const file of Array.from(files)) {
    if (!file.type.startsWith('image/')) continue
    try {
      const dataUrl = await fileToDataUrl(file, { maxEdge: 1600, maxBytes: 7 * 1024 * 1024 })
      pendingImages.value.push(dataUrl)
    } catch (e) {
      chat.setError(`图片读取失败：${(e as Error).message}`)
    }
  }
}

async function handleDocFiles(files: FileList | null) {
  if (!files || files.length === 0) return
  docUploading.value = true
  try {
    for (const file of Array.from(files)) {
      try {
        const result = await extractDocument(file)
        const block = `\n\n[文档: ${result.filename} · 估算 ${result.estimated_tokens} tokens]\n${result.text}\n`
        text.value = (text.value + block).trim()
        await autoResize()
      } catch (e) {
        chat.setError(`无法解析 ${file.name}：${(e as Error).message}`)
      }
    }
  } finally {
    docUploading.value = false
  }
}

async function onPaste(e: ClipboardEvent) {
  const files = extractImagesFromClipboard(e)
  if (files.length > 0) {
    e.preventDefault()
    await handleImageFiles(files)
  }
}

async function onDrop(e: DragEvent) {
  e.preventDefault()
  isDragging.value = false
  const files = e.dataTransfer?.files
  if (!files) return
  const images: File[] = []
  const docs: File[] = []
  for (const f of Array.from(files)) {
    if (f.type.startsWith('image/')) images.push(f)
    else docs.push(f)
  }
  if (images.length) await handleImageFiles(images)
  if (docs.length) {
    const dt = new DataTransfer()
    docs.forEach((f) => dt.items.add(f))
    await handleDocFiles(dt.files)
  }
}

function onDragOver(e: DragEvent) {
  e.preventDefault()
  isDragging.value = true
}

function onDragLeave() {
  isDragging.value = false
}

function pickImage() {
  imageInputRef.value?.click()
}

function pickDoc() {
  docInputRef.value?.click()
}

async function onImageInput(e: Event) {
  const input = e.target as HTMLInputElement
  await handleImageFiles(input.files)
  input.value = ''
}

async function onDocInput(e: Event) {
  const input = e.target as HTMLInputElement
  await handleDocFiles(input.files)
  input.value = ''
}

function removeImage(index: number) {
  pendingImages.value.splice(index, 1)
}

async function openStickers() {
  if (!stickersOpen.value && !stickersLoaded) {
    await loadStickers()
  }
  stickersOpen.value = !stickersOpen.value
}

function insertSticker(s: Sticker) {
  text.value = `${text.value}[sticker:${s.name}]`
  stickersOpen.value = false
  void autoResize()
}

function submit() {
  if (!canSend.value) return
  const value = text.value.trim()
  const imgs = [...pendingImages.value]
  text.value = ''
  pendingImages.value = []
  void autoResize()
  emit('send', value, imgs)
}

function stop() {
  emit('stop')
}
</script>

<template>
  <div
    class="relative px-3 py-3 sm:px-6 sm:py-4 border-t border-ink/5 dark:border-night-text/10
           bg-paper/80 dark:bg-night-bg/80 backdrop-blur-md"
    @drop="onDrop"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
  >
    <div
      v-if="isDragging"
      class="absolute inset-0 z-10 flex items-center justify-center pointer-events-none
             bg-accent-soft/20 dark:bg-night-accent-soft/20 border-2 border-dashed
             border-accent dark:border-night-accent rounded-lg m-2"
    >
      <span class="text-accent dark:text-night-accent">松开放到对话里（图片或文档）</span>
    </div>

    <div class="max-w-3xl mx-auto">
      <!-- 表情包选择浮层 -->
      <div
        v-if="stickersOpen"
        class="mb-2 p-2 rounded-xl bg-paper-soft dark:bg-night-bg-soft
               border border-ink/10 dark:border-night-text/10 shadow-letter"
      >
        <div v-if="myStickers.length === 0" class="text-sm text-ink-soft dark:text-night-text-soft p-2">
          你还没添加表情包。请到「设置 → 表情包」上传。
        </div>
        <div v-else class="flex flex-wrap gap-2 max-h-[160px] overflow-y-auto">
          <button
            v-for="s in myStickers"
            :key="s.name"
            class="w-16 h-16 rounded-lg overflow-hidden bg-paper dark:bg-night-bg
                   border border-ink/10 dark:border-night-text/10 hover:scale-105 transition-transform"
            :title="`${s.name}：${s.description}`"
            @click="insertSticker(s)"
          >
            <img :src="s.image_url" alt="" class="w-full h-full object-cover" />
          </button>
        </div>
      </div>

      <!-- 待发送图片预览 -->
      <div
        v-if="pendingImages.length > 0"
        class="flex flex-wrap gap-2 mb-2"
      >
        <div
          v-for="(src, i) in pendingImages"
          :key="i"
          class="relative w-20 h-20 rounded-lg overflow-hidden bg-paper-soft dark:bg-night-bg-soft
                 border border-ink/10 dark:border-night-text/10 group"
        >
          <img :src="src" class="w-full h-full object-cover" alt="待发送图片" />
          <button
            class="absolute top-1 right-1 p-0.5 rounded-full bg-ink/60 text-paper-soft
                   opacity-0 group-hover:opacity-100 transition-opacity"
            @click="removeImage(i)"
            aria-label="删除图片"
          >
            <X :size="12" />
          </button>
        </div>
      </div>

      <div
        class="flex items-end gap-1 rounded-2xl px-3 py-2
               bg-paper-soft dark:bg-night-bg-soft shadow-letter
               border border-ink/5 dark:border-night-text/10"
      >
        <input
          ref="imageInputRef"
          type="file"
          accept="image/*"
          multiple
          class="hidden"
          @change="onImageInput"
        />
        <input
          ref="docInputRef"
          type="file"
          accept=".txt,.md,.pdf,.docx,.xlsx,.csv,.json,.html,.htm,.log,.yml,.yaml,.xml,.ini"
          multiple
          class="hidden"
          @change="onDocInput"
        />
        <button
          class="p-2 rounded-full text-ink-soft dark:text-night-text-soft
                 hover:text-ink dark:hover:text-night-text transition-colors"
          title="添加图片（也可粘贴或拖拽）"
          @click="pickImage"
        >
          <ImagePlus :size="20" />
        </button>
        <button
          class="p-2 rounded-full text-ink-soft dark:text-night-text-soft
                 hover:text-ink dark:hover:text-night-text transition-colors disabled:opacity-50"
          :disabled="docUploading"
          :title="docUploading ? '提取中...' : '上传文档（txt/md/pdf/docx/xlsx 等）'"
          @click="pickDoc"
        >
          <FileText :size="20" />
        </button>
        <button
          class="p-2 rounded-full text-ink-soft dark:text-night-text-soft
                 hover:text-ink dark:hover:text-night-text transition-colors"
          title="发送表情包"
          @click="openStickers"
        >
          <StickyNote :size="20" />
        </button>
        <textarea
          ref="textareaRef"
          v-model="text"
          rows="1"
          placeholder="写点什么...（可粘贴/拖拽图片或文档）"
          class="flex-1 resize-none bg-transparent outline-none px-2 py-2 text-chat
                 text-ink dark:text-night-text placeholder:text-ink-soft/60
                 dark:placeholder:text-night-text-soft/50 leading-relaxed max-h-[200px]"
          @keydown="onKeyDown"
          @input="autoResize"
          @paste="onPaste"
        />
        <button
          v-if="chat.isGenerating"
          class="p-2 rounded-full text-warning hover:bg-warning/10 transition-colors"
          title="停止生成"
          @click="stop"
        >
          <StopCircle :size="22" />
        </button>
        <button
          class="p-2 rounded-full transition-colors"
          :class="
            canSend
              ? 'text-accent hover:bg-accent/10 dark:text-night-accent dark:hover:bg-night-accent/10'
              : 'text-ink-soft/40 dark:text-night-text-soft/40 cursor-not-allowed'
          "
          :disabled="!canSend"
          title="发送（Enter）"
          @click="submit"
        >
          <Send :size="22" />
        </button>
      </div>
      <p class="text-[11px] text-center text-ink-soft/60 dark:text-night-text-soft/60 mt-2 no-select">
        Enter 发送 · Shift+Enter 换行 · 支持粘贴/拖拽图片或文档
      </p>
    </div>
  </div>
</template>
