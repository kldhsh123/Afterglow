<script setup lang="ts">
// 整体外壳：背景 + 顶栏 + 主内容
// 注意：根容器必须锁死视口高度（h-dvh / h-screen），否则页面会整体滚动，
// 导致 MessageList 内部"回到底部"按钮和工具栏跟着内容一起滚走，用户上滑后碰不到。
import AmbientCanvas from './AmbientCanvas.vue'
import ChatHeader from '@/components/chat/ChatHeader.vue'
import MemoryModal from '@/components/memory/MemoryModal.vue'
</script>

<template>
  <!-- height 双声明：100vh 是 fallback；支持 dvh 的浏览器（Chrome 108+/Safari 16.4+/Firefox 109+）
       自动用 100dvh，避免移动端地址栏出现/收起导致视口高度跳变。 -->
  <div
    class="relative overflow-hidden paper-bg text-ink dark:text-night-text flex flex-col"
    style="height: 100vh; height: 100dvh;"
  >
    <AmbientCanvas />
    <ChatHeader />
    <main class="relative flex-1 min-h-0 overflow-hidden">
      <slot />
    </main>
    <MemoryModal />
  </div>
</template>
