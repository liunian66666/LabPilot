import { createRouter, createWebHistory } from 'vue-router'
import { isLoggedIn } from '../utils/auth'

// 视图全部懒加载，按路由自动分包，减小首屏体积
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    {
      path: '/login',
      component: () => import('../views/LoginView.vue'),
      meta: { title: '登录', public: true },
    },
    {
      path: '/chat',
      component: () => import('../views/ChatView.vue'),
      meta: { title: '对话', fullBleed: true },
    },
    {
      path: '/todos',
      component: () => import('../views/TodosView.vue'),
      meta: { title: '四维待办' },
    },
    {
      path: '/extract',
      component: () => import('../views/ExtractView.vue'),
      meta: { title: '纪要抽任务' },
    },
    {
      path: '/progress',
      component: () => import('../views/ProgressView.vue'),
      meta: { title: '进展记录' },
    },
    {
      path: '/report',
      component: () => import('../views/ReportView.vue'),
      meta: { title: '周报草稿' },
    },
    {
      path: '/knowledge',
      component: () => import('../views/KnowledgeView.vue'),
      meta: { title: '资料库' },
    },
    {
      path: '/zotero',
      component: () => import('../views/ZoteroView.vue'),
      meta: { title: 'Zotero 文献' },
    },
    {
      path: '/approvals',
      component: () => import('../views/ApprovalsView.vue'),
      meta: { title: '周报审批' },
    },
    {
      path: '/reminders',
      component: () => import('../views/RemindersView.vue'),
      meta: { title: '截止提醒' },
    },
  ],
})

// 全局鉴权守卫：未登录访问受保护页面 → 跳登录（带 redirect 回跳）
router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!isLoggedIn()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
