<script setup lang="ts">
// 顶部提示：检索失败 / 后端不可达 / 等
import { useChatStore } from '@/stores/chat'
import { AlertCircle, X } from 'lucide-vue-next'
import { computed } from 'vue'
import { useLocale } from '@/composables/useLocale'

const chat = useChatStore()
const { t } = useLocale()
const visible = computed(() => !!chat.lastError)
</script>

<template>
  <Transition
    enter-active-class="transition-all duration-300 ease-out"
    enter-from-class="opacity-0 -translate-y-2"
    leave-active-class="transition-all duration-200 ease-in"
    leave-to-class="opacity-0 -translate-y-2"
  >
    <div
      v-if="visible"
      class="mx-4 mt-3 p-3 rounded-xl bg-warning/10 text-warning text-sm
             flex items-start gap-2 border border-warning/20"
    >
      <AlertCircle :size="18" class="mt-0.5 shrink-0" />
      <p class="flex-1">{{ chat.lastError }}</p>
      <button
        class="p-1 rounded-full hover:bg-warning/20"
        @click="chat.setError(null)"
        :aria-label="t('common.dismiss')"
      >
        <X :size="14" />
      </button>
    </div>
  </Transition>
</template>
