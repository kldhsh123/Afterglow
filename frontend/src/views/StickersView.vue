<script setup lang="ts">
// 表情包管理页：列表 / 上传 / 编辑 / 删除
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ChevronLeft,
  ImagePlus,
  Save,
  Trash2,
  X,
} from 'lucide-vue-next'
import {
  createSticker,
  deleteSticker,
  listStickers,
  type Sticker,
  updateSticker,
} from '@/api/extra'
import { fileToDataUrl } from '@/composables/imageUpload'
import { useLocale } from '@/composables/useLocale'

type Owner = 'ai' | 'self' | 'shared'

const router = useRouter()
const { t } = useLocale()
const stickers = ref<Sticker[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const ownerFilter = ref<'all' | Owner>('all')

// 上传表单
const newName = ref('')
const newDescription = ref('')
const newOwner = ref<Owner>('shared')
const newDataUrl = ref<string | null>(null)
const newTags = ref('')
const fileInputRef = ref<HTMLInputElement | null>(null)
const saving = ref(false)

// 编辑中的表情
const editing = ref<{ name: string; description: string; owner: Owner; tags: string } | null>(null)

const filtered = computed(() => {
  if (ownerFilter.value === 'all') return stickers.value
  return stickers.value.filter((s) => s.owner === ownerFilter.value)
})

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const r = await listStickers()
    stickers.value = r.items
  } catch (e) {
    errorMsg.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

onMounted(load)

function back() {
  router.push('/settings')
}

function pickFile() {
  fileInputRef.value?.click()
}

async function onFileInput(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    newDataUrl.value = await fileToDataUrl(file, { maxEdge: 480, maxBytes: 1.5 * 1024 * 1024 })
    if (!newName.value) newName.value = file.name.replace(/\.[^.]+$/, '')
  } catch (e) {
    errorMsg.value = (e as Error).message
  }
}

async function submitNew() {
  if (!newDataUrl.value) {
    errorMsg.value = t('stickers.chooseImage')
    return
  }
  if (!newName.value.trim() || !newDescription.value.trim()) {
    errorMsg.value = t('stickers.required')
    return
  }
  saving.value = true
  errorMsg.value = null
  try {
    await createSticker({
      name: newName.value.trim(),
      description: newDescription.value.trim(),
      data_url: newDataUrl.value,
      owner: newOwner.value,
      tags: newTags.value
        .split(/[,，\s]+/)
        .map((t) => t.trim())
        .filter(Boolean),
    })
    // 清空表单
    newName.value = ''
    newDescription.value = ''
    newDataUrl.value = null
    newTags.value = ''
    newOwner.value = 'shared'
    await load()
  } catch (e) {
    errorMsg.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

function startEdit(s: Sticker) {
  editing.value = {
    name: s.name,
    description: s.description,
    owner: s.owner,
    tags: s.tags.join(', '),
  }
}

async function saveEdit() {
  if (!editing.value) return
  try {
    await updateSticker(editing.value.name, {
      description: editing.value.description,
      owner: editing.value.owner,
      tags: editing.value.tags
        .split(/[,，\s]+/)
        .map((t) => t.trim())
        .filter(Boolean),
    })
    editing.value = null
    await load()
  } catch (e) {
    errorMsg.value = (e as Error).message
  }
}

async function remove(name: string) {
  if (!confirm(t('stickers.removeConfirm', { name }))) return
  try {
    await deleteSticker(name)
    await load()
  } catch (e) {
    errorMsg.value = (e as Error).message
  }
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="max-w-3xl mx-auto px-4 py-6 sm:py-10 space-y-6">
      <header class="flex items-center gap-2">
        <button
          class="p-2 -ml-2 rounded-full text-ink-soft hover:text-ink
                 dark:text-night-text-soft dark:hover:text-night-text"
          @click="back"
        >
          <ChevronLeft :size="20" />
        </button>
        <h1 class="text-xl font-medium">{{ t('stickers.title') }}</h1>
      </header>

      <p v-if="errorMsg" class="text-sm text-warning bg-warning/10 px-3 py-2 rounded-lg">
        {{ errorMsg }}
      </p>

      <!-- 上传新表情 -->
      <section
        class="rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft shadow-letter
               border border-ink/5 dark:border-night-text/10 space-y-3"
      >
        <h2 class="text-base font-medium">{{ t('stickers.add') }}</h2>
        <div class="flex items-start gap-4">
          <div
            class="w-24 h-24 shrink-0 rounded-lg border border-dashed border-ink/20 dark:border-night-text/20
                   flex items-center justify-center cursor-pointer bg-paper dark:bg-night-bg
                   hover:border-accent dark:hover:border-night-accent transition-colors"
            @click="pickFile"
          >
            <img v-if="newDataUrl" :src="newDataUrl" class="max-w-full max-h-full" :alt="t('stickers.preview')" />
            <ImagePlus v-else :size="28" class="text-ink-soft dark:text-night-text-soft" />
          </div>
          <input
            ref="fileInputRef"
            type="file"
            accept="image/*"
            class="hidden"
            @change="onFileInput"
          />
          <div class="flex-1 space-y-2">
            <input
              v-model="newName"
              :placeholder="t('stickers.namePlaceholder')"
              maxlength="32"
              class="w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10 outline-none
                     focus:ring-2 focus:ring-accent-soft"
            />
            <input
              v-model="newDescription"
              :placeholder="t('stickers.descriptionPlaceholder')"
              maxlength="200"
              class="w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10 outline-none
                     focus:ring-2 focus:ring-accent-soft"
            />
            <div class="flex items-center gap-2">
              <select
                v-model="newOwner"
                class="px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                       border border-ink/10 dark:border-night-text/10 outline-none"
              >
                <option value="shared">{{ t('stickers.shared') }}</option>
                <option value="ai">{{ t('stickers.aiOnly') }}</option>
                <option value="self">{{ t('stickers.selfOnly') }}</option>
              </select>
              <input
                v-model="newTags"
                :placeholder="t('stickers.tagsPlaceholder')"
                class="flex-1 px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                       border border-ink/10 dark:border-night-text/10 outline-none
                       focus:ring-2 focus:ring-accent-soft"
              />
            </div>
          </div>
        </div>
        <button
          :disabled="saving"
          class="w-full py-2.5 rounded-full bg-accent text-paper-soft
                 hover:bg-accent/90 transition-colors disabled:opacity-50 text-sm"
          @click="submitNew"
        >
          {{ saving ? t('stickers.uploading') : t('stickers.save') }}
        </button>
      </section>

      <!-- 现有列表 -->
      <section
        class="rounded-2xl p-4 bg-paper-soft dark:bg-night-bg-soft shadow-letter
               border border-ink/5 dark:border-night-text/10"
      >
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-base font-medium">{{ t('stickers.existing', { count: stickers.length }) }}</h2>
          <select
            v-model="ownerFilter"
            class="text-xs px-2 py-1 rounded-lg bg-paper dark:bg-night-bg
                   border border-ink/10 dark:border-night-text/10 outline-none"
          >
            <option value="all">{{ t('stickers.all') }}</option>
            <option value="shared">{{ t('stickers.sharedShort') }}</option>
            <option value="ai">{{ t('stickers.aiShort') }}</option>
            <option value="self">{{ t('stickers.selfShort') }}</option>
          </select>
        </div>
        <div v-if="loading" class="text-center text-ink-soft dark:text-night-text-soft py-6">
          {{ t('stickers.loading') }}
        </div>
        <div v-else-if="filtered.length === 0" class="text-center text-ink-soft dark:text-night-text-soft py-6">
          {{ t('stickers.empty') }}
        </div>
        <div v-else class="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <div
            v-for="s in filtered"
            :key="s.name"
            class="relative rounded-xl border border-ink/10 dark:border-night-text/10
                   bg-paper dark:bg-night-bg p-2 flex gap-2"
          >
            <img :src="s.image_url" class="w-16 h-16 rounded-md object-cover" alt="" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium truncate" :title="s.name">{{ s.name }}</div>
              <div class="text-xs text-ink-soft dark:text-night-text-soft line-clamp-2">
                {{ s.description }}
              </div>
              <div class="flex items-center gap-2 mt-1">
                <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-accent-soft/30 dark:bg-night-accent-soft/30 text-accent dark:text-night-accent">
                  {{ s.owner === 'shared' ? t('stickers.sharedShort') : s.owner === 'ai' ? t('stickers.aiShort') : t('stickers.selfShort') }}
                </span>
                <button
                  class="ml-auto p-1 rounded hover:bg-paper-shade dark:hover:bg-night-bubble-user"
                  :title="t('stickers.edit')"
                  @click="startEdit(s)"
                >
                  <Save :size="14" />
                </button>
                <button
                  class="p-1 rounded text-warning hover:bg-warning/10"
                  :title="t('stickers.remove')"
                  @click="remove(s.name)"
                >
                  <Trash2 :size="14" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- 编辑浮窗 -->
    <Teleport to="body">
      <div
        v-if="editing"
        class="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 dark:bg-black/60 p-4"
        @click.self="editing = null"
      >
        <div
          class="w-full max-w-md bg-paper-soft dark:bg-night-bg-soft rounded-2xl shadow-letter-strong
                 p-5 space-y-3"
        >
          <header class="flex items-center justify-between">
            <h3 class="text-base font-medium">{{ t('stickers.editTitle', { name: editing.name }) }}</h3>
            <button class="p-1 rounded-full" @click="editing = null"><X :size="18" /></button>
          </header>
          <label class="block">
            <span class="text-sm">{{ t('stickers.description') }}</span>
            <input
              v-model="editing.description"
              maxlength="200"
              class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10 outline-none"
            />
          </label>
          <label class="block">
            <span class="text-sm">{{ t('stickers.owner') }}</span>
            <select
              v-model="editing.owner"
              class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10 outline-none"
            >
              <option value="shared">{{ t('stickers.sharedShort') }}</option>
              <option value="ai">{{ t('stickers.aiShort') }}</option>
              <option value="self">{{ t('stickers.selfShort') }}</option>
            </select>
          </label>
          <label class="block">
            <span class="text-sm">{{ t('stickers.tags') }}</span>
            <input
              v-model="editing.tags"
              class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10 outline-none"
            />
          </label>
          <button
            class="w-full py-2.5 rounded-full bg-accent text-paper-soft hover:bg-accent/90"
            @click="saveEdit"
          >
            {{ t('common.save') }}
          </button>
        </div>
      </div>
    </Teleport>
  </div>
</template>
