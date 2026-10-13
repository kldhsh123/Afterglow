<script setup lang="ts">
// 首次启动引导：让用户填写名字、关系、后端地址
import { ref } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { Heart } from 'lucide-vue-next'
import { useLocale } from '@/composables/useLocale'

const settings = useSettingsStore()
const { t } = useLocale()
const step = ref(0)

const relations = [
  { value: '朋友', label: '朋友' },
  { value: '恋人', label: '恋人' },
  { value: '亲人', label: '亲人' },
  { value: '同事', label: '同事' },
]

function finish() {
  settings.onboardingDone = true
}

function next() {
  step.value += 1
}

const canNextFromName = () =>
  settings.selfName.trim().length > 0 && settings.friendName.trim().length > 0
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-40 paper-bg flex items-center justify-center p-6">
      <div class="max-w-md w-full bg-paper-soft dark:bg-night-bg-soft rounded-2xl
                  shadow-letter-strong p-7">
        <div class="text-center mb-5">
          <div class="inline-flex w-14 h-14 rounded-full bg-accent-soft/30 dark:bg-night-accent-soft/30
                      items-center justify-center text-accent dark:text-night-accent mb-3">
            <Heart :size="28" />
          </div>
          <h2 class="text-xl font-medium">{{ t('onboarding.welcome', { name: settings.appName }) }}</h2>
          <p class="text-sm text-ink-soft dark:text-night-text-soft mt-1">
            {{ t('onboarding.slogan') }}
          </p>
        </div>

        <!-- 第 0 步：欢迎 -->
        <div v-if="step === 0" class="space-y-4">
          <p class="text-sm leading-relaxed">
            {{ t('onboarding.intro') }}
          </p>
          <button
            class="w-full py-2.5 rounded-full bg-accent text-paper-soft
                   hover:bg-accent/90 transition-colors text-sm"
            @click="next"
          >
            {{ t('onboarding.start') }}
          </button>
        </div>

        <!-- 第 1 步：身份 -->
        <div v-else-if="step === 1" class="space-y-4">
          <label class="block">
            <span class="text-sm">{{ t('onboarding.yourName') }}</span>
            <input
              v-model="settings.selfName"
              type="text"
              :placeholder="t('onboarding.yourPlaceholder')"
              class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10
                     text-ink dark:text-night-text outline-none
                     focus:ring-2 focus:ring-accent-soft"
            />
          </label>
          <label class="block">
            <span class="text-sm">{{ t('onboarding.theirName') }}</span>
            <input
              v-model="settings.friendName"
              type="text"
              :placeholder="t('onboarding.theirPlaceholder')"
              class="mt-1 w-full px-3 py-2 rounded-lg bg-paper dark:bg-night-bg
                     border border-ink/10 dark:border-night-text/10
                     text-ink dark:text-night-text outline-none
                     focus:ring-2 focus:ring-accent-soft"
            />
          </label>
          <div class="flex gap-2">
            <button
              class="flex-1 py-2.5 rounded-full border border-ink/10 dark:border-night-text/10
                     text-ink-soft dark:text-night-text-soft hover:text-ink dark:hover:text-night-text"
              @click="step = 0"
            >
              {{ t('onboarding.back') }}
            </button>
            <button
              :disabled="!canNextFromName()"
              class="flex-1 py-2.5 rounded-full bg-accent text-paper-soft
                     hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed"
              @click="next"
            >
              {{ t('onboarding.next') }}
            </button>
          </div>
        </div>

        <!-- 第 2 步：关系 -->
        <div v-else-if="step === 2" class="space-y-4">
          <p class="text-sm">{{ t('onboarding.relationship') }}</p>
          <div class="grid grid-cols-2 gap-2">
            <button
              v-for="r in relations"
              :key="r.value"
              class="py-3 rounded-xl border text-sm transition-colors"
              :class="
                settings.relationshipDescription === r.value
                  ? 'border-accent bg-accent/10 text-accent dark:border-night-accent dark:text-night-accent dark:bg-night-accent/10'
                  : 'border-ink/10 dark:border-night-text/10 text-ink dark:text-night-text hover:bg-paper dark:hover:bg-night-bg'
              "
              @click="settings.relationshipDescription = r.value"
            >
              {{ r.label }}
            </button>
          </div>
          <p class="text-xs text-ink-soft dark:text-night-text-soft">
            {{ t('onboarding.relationshipHint') }}
          </p>
          <div class="flex gap-2">
            <button
              class="flex-1 py-2.5 rounded-full border border-ink/10 dark:border-night-text/10
                     text-ink-soft dark:text-night-text-soft hover:text-ink dark:hover:text-night-text"
              @click="step = 1"
            >
              {{ t('onboarding.back') }}
            </button>
            <button
              class="flex-1 py-2.5 rounded-full bg-accent text-paper-soft hover:bg-accent/90"
              @click="finish"
            >
              {{ t('onboarding.startChat') }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
