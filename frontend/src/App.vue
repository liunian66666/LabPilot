<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import LoadingBar from './components/LoadingBar.vue'
import { clearAuth, getUsername } from './utils/auth'

const route = useRoute()
const router = useRouter()
const title = computed(() => (route.meta.title as string) || 'LabPilot')
// 全出血页面（如对话）：隐藏页头，内容占满主区
const fullBleed = computed(() => route.meta.fullBleed === true)
// 登录等公开页：不显示侧栏与主框架
const isPublic = computed(() => route.meta.public === true)
const menuOpen = ref(false)

// 当前登录用户（登录页 setAuth 后进入，此处读一次即可）
const username = ref(getUsername())
function logout() {
  clearAuth()
  router.replace('/login')
}

// 桌面端侧栏收起（状态持久化；移动端抽屉不受影响）
const SIDEBAR_KEY = 'labpilot_sidebar_collapsed'
const sidebarCollapsed = ref(localStorage.getItem(SIDEBAR_KEY) === '1')

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem(SIDEBAR_KEY, sidebarCollapsed.value ? '1' : '0')
}

watch(
  () => route.fullPath,
  () => {
    menuOpen.value = false
    username.value = getUsername()
  },
)

// 路由切换时滚动回顶部
watch(
  () => route.path,
  () => {
    document.querySelector('.main')?.scrollTo({ top: 0 })
  },
)

interface NavItem {
  to: string
  label: string
  icon: string
}

const nav: NavItem[] = [
  { to: '/chat', label: '对话', icon: 'chat' },
  { to: '/todos', label: '四维待办', icon: 'todos' },
  { to: '/extract', label: '纪要抽任务', icon: 'extract' },
  { to: '/progress', label: '进展记录', icon: 'progress' },
  { to: '/report', label: '周报草稿', icon: 'report' },
  { to: '/knowledge', label: '资料库 RAG', icon: 'knowledge' },
  { to: '/zotero', label: 'Zotero 文献', icon: 'zotero' },
  { to: '/approvals', label: '周报审批', icon: 'send' },
  { to: '/reminders', label: '截止提醒', icon: 'bell' },
]
</script>

<template>
  <RouterView v-if="isPublic" />
  <div v-else class="layout" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <LoadingBar :active="false" />

    <!-- 移动端顶栏 -->
    <header class="topbar">
      <button class="menu-btn" aria-label="打开导航" @click="menuOpen = !menuOpen">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path
            d="M4 7h16M4 12h16M4 17h16"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
          />
        </svg>
      </button>
      <span class="topbar-title">{{ title }}</span>
    </header>
    <div v-if="menuOpen" class="scrim" @click="menuOpen = false"></div>

    <aside class="sidebar" :class="{ open: menuOpen, collapsed: sidebarCollapsed }">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">L</div>
        <div class="brand-text">Lab<span>Pilot</span></div>
      </div>
      <p v-if="!sidebarCollapsed" class="brand-sub">课题组科研事务助理</p>

      <nav class="nav" aria-label="主导航">
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          class="nav-item"
          :to="item.to"
          :title="sidebarCollapsed ? item.label : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path
              v-if="item.icon === 'chat'"
              d="M4 5h16v11H9l-5 4V5z"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            />
            <path
              v-else-if="item.icon === 'todos'"
              d="M4 6h10M4 12h10M4 18h10M17 5l2 2 3-3M17 11l2 2 3-3"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
            <path
              v-else-if="item.icon === 'extract'"
              d="M7 4h10v4l-4 4 4 4v4H7v-4l4-4-4-4V4z"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            />
            <path
              v-else-if="item.icon === 'progress'"
              d="M4 20V10M10 20V4M16 20v-8M22 20H2"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
            />
            <path
              v-else-if="item.icon === 'report'"
              d="M6 3h9l4 4v14H6V3zM14 3v5h5M9 12h6M9 16h6"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            />
            <path
              v-else-if="item.icon === 'knowledge'"
              d="M5 4h6v16H5zM13 4h6v16h-6zM11 8h2M11 14h2"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            />
            <path
              v-else-if="item.icon === 'zotero'"
              d="M4 5h16v14H4zM8 9h8l-8 6h8"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
            <path
              v-else-if="item.icon === 'send'"
              d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            />
            <path
              v-else-if="item.icon === 'bell'"
              d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
            <path
              v-else
              d="M4 6h16v12H4zM4 7l8 6 8-6"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            />
          </svg>
          <span class="nav-label">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="sidebar-bottom">
        <div class="user-box">
          <div class="user-meta">
            <span class="user-avatar">{{ username.slice(0, 1).toUpperCase() }}</span>
            <span class="user-name">{{ username }}</span>
          </div>
          <button class="logout-btn" title="退出登录" @click="logout">退出</button>
        </div>
        <div class="sidebar-foot">
          外发必须人工确认<br />拒绝路径不会发送
        </div>
        <button
          class="side-toggle"
          :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
          :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
          @click="toggleSidebar"
        >
          <svg
            viewBox="0 0 24 24"
            width="16"
            height="16"
            aria-hidden="true"
            :style="{ transform: sidebarCollapsed ? 'rotate(180deg)' : 'none' }"
          >
            <path
              d="M11 6l-6 6 6 6M18 6l-6 6 6 6"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              fill="none"
            />
          </svg>
        </button>
      </div>
    </aside>

    <main class="main" :class="{ 'full-bleed': fullBleed }">
      <div v-if="!fullBleed" class="page-head">
        <h1 class="page-title">{{ title }}</h1>
        <p class="page-desc">先做事、再留痕；资料有出处，发送先确认。</p>
      </div>
      <RouterView v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 248px 1fr;
  min-height: 100vh;
  transition: grid-template-columns 0.2s ease;
}

/* ---------- 顶栏（仅移动端显示） ---------- */
.topbar {
  display: none;
}

.scrim {
  display: none;
}

/* ---------- 侧边栏 ---------- */
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 26px 16px 20px;
  background:
    radial-gradient(120% 60% at 0% 0%, rgba(79, 140, 255, 0.16), transparent 60%),
    linear-gradient(180deg, #0c1729 0%, #0f1e38 100%);
  color: #d6e2f8;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.side-toggle {
  flex: none;
  align-self: center;
  width: 30px;
  height: 30px;
  min-height: 30px;
  padding: 0;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  color: #c4d3ee;
}

.side-toggle:hover {
  filter: none;
  transform: none;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  box-shadow: none;
}

.side-toggle svg {
  transition: transform 0.18s ease;
}

.brand-mark {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  background: linear-gradient(135deg, #3b74e0, #6ea3ff);
  color: #fff;
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 18px;
  box-shadow: 0 4px 14px rgba(78, 132, 255, 0.35);
}

.brand-text {
  font-family: var(--font-display);
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #f2f6ff;
}

.brand-text span {
  color: #8eb6ff;
}

.brand-sub {
  margin: 10px 2px 18px;
  font-size: 12px;
  opacity: 0.7;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 10px;
  color: #c4d3ee;
  text-decoration: none;
  font-size: 14px;
  transition: background 0.15s ease, color 0.15s ease;
}

.nav-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: 20%;
  bottom: 20%;
  width: 3px;
  border-radius: 999px;
  background: transparent;
  transition: background 0.15s ease;
}

.nav-icon {
  width: 17px;
  height: 17px;
  flex: none;
  opacity: 0.85;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.07);
  color: #fff;
}

.nav-item.router-link-active {
  background: rgba(96, 150, 255, 0.16);
  color: #fff;
}

.nav-item.router-link-active::before {
  background: #6ea3ff;
}

.nav-item:focus-visible {
  outline: 2px solid #8eb6ff;
  outline-offset: 2px;
}

.sidebar-bottom {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sidebar-foot {
  padding: 12px 14px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  font-size: 11px;
  line-height: 1.6;
  opacity: 0.55;
}

.user-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.05);
}

.user-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.user-avatar {
  flex: none;
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: linear-gradient(135deg, #3b74e0, #6ea3ff);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
}

.user-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: #e6eeff;
}

.logout-btn {
  flex: none;
  padding: 5px 10px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 7px;
  background: transparent;
  color: #c4d3ee;
  font-size: 12px;
  cursor: pointer;
}

.logout-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

/* ---------- 桌面端侧栏收起态 ---------- */
.layout.sidebar-collapsed {
  grid-template-columns: 64px 1fr;
}

.sidebar {
  transition: padding 0.18s ease;
}

.sidebar.collapsed {
  padding: 26px 10px 20px;
}

.sidebar.collapsed .brand {
  flex-direction: column;
  gap: 10px;
  margin-bottom: 24px;
}

.sidebar.collapsed .brand-text,
.sidebar.collapsed .brand-sub,
.sidebar.collapsed .nav-label,
.sidebar.collapsed .sidebar-foot,
.sidebar.collapsed .user-name,
.sidebar.collapsed .logout-btn {
  display: none;
}

.sidebar.collapsed .user-box {
  justify-content: center;
  padding: 8px;
}

/* 收起态：底部按钮居中，品牌块只留 L 标 */
.sidebar.collapsed .nav-item {
  justify-content: center;
  padding: 11px;
}

.sidebar.collapsed .brand-mark {
  box-shadow: 0 4px 14px rgba(78, 132, 255, 0.35);
}

/* ---------- 主区 ---------- */
.main {
  padding: 30px clamp(20px, 4vw, 44px) 56px;
  overflow-x: hidden;
}

.main.full-bleed {
  padding: 0;
  overflow: hidden;
}

.page-head {
  margin-bottom: 24px;
}

.page-title {
  margin: 0 0 6px;
  font-family: var(--font-display);
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.page-desc {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
}

/* 路由切换动效 */
.page-enter-active,
.page-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.page-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.page-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (prefers-reduced-motion: reduce) {
  .page-enter-active,
  .page-leave-active {
    transition: none;
  }

  .sidebar,
  .side-toggle svg {
    transition: none;
  }

  .layout {
    transition: none;
  }
}

/* ---------- 移动端：侧栏转抽屉 ---------- */
@media (max-width: 960px) {
  .layout {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
  }

  .topbar {
    position: sticky;
    top: 0;
    z-index: 30;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    background: rgba(12, 23, 41, 0.92);
    backdrop-filter: blur(10px);
    color: #e6eeff;
  }

  .menu-btn {
    display: grid;
    place-items: center;
    width: 40px;
    height: 40px;
    padding: 0;
    background: rgba(255, 255, 255, 0.08);
    color: #e6eeff;
  }

  .topbar-title {
    font-size: 15px;
    font-weight: 600;
  }

  .scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 40;
    background: rgba(10, 16, 30, 0.5);
  }

  .sidebar {
    position: fixed;
    z-index: 50;
    top: 0;
    bottom: 0;
    left: 0;
    width: min(78vw, 300px);
    transform: translateX(-105%);
    transition: transform 0.22s ease;
  }

  .sidebar.open {
    transform: translateX(0);
  }

  /* 移动端抽屉始终完整展示，不受收起态影响 */
  .sidebar.collapsed {
    width: min(78vw, 300px);
    padding: 26px 16px 20px;
  }

  .sidebar.collapsed .brand {
    flex-direction: row;
  }

  .sidebar.collapsed .brand-sub,
  .sidebar.collapsed .brand-text,
  .sidebar.collapsed .nav-label,
  .sidebar.collapsed .sidebar-foot {
    display: block;
  }

  .sidebar.collapsed .nav-item {
    justify-content: flex-start;
    padding: 11px 14px;
  }

  .main {
    padding: 22px 18px 48px;
  }
}
</style>
