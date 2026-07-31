import { createRouter, createWebHashHistory } from 'vue-router'

const HomeView = () => import('@/views/HomeView.vue')
const SettingsView = () => import('@/views/SettingsView.vue')
const StickersView = () => import('@/views/StickersView.vue')
const TimelineView = () => import('@/views/TimelineView.vue')
const ReportView = () => import('@/views/ReportView.vue')

export const router = createRouter({
  // 用 hash 路由，避免单机部署时需要服务器 rewrite
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: HomeView, name: 'home' },
    { path: '/settings', component: SettingsView, name: 'settings' },
    { path: '/stickers', component: StickersView, name: 'stickers' },
    { path: '/timeline', component: TimelineView, name: 'timeline' },
    { path: '/report', component: ReportView, name: 'report' },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
