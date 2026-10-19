<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type ZoteroConnection, type ZoteroItem } from '../api/client'
import EmptyState from '../components/EmptyState.vue'
import LoadingBar from '../components/LoadingBar.vue'

const connection = ref<ZoteroConnection | null>(null)
const apiKey = ref('')
const query = ref('')
const items = ref<ZoteroItem[]>([])
const searched = ref(false)
const loading = ref(false)
const saving = ref(false)
const searching = ref(false)
const message = ref('')
const error = ref('')

async function loadConnection() {
  loading.value = true
  error.value = ''
  try {
    connection.value = await api.zoteroConnection()
  } catch {
    connection.value = null
    error.value = '暂时无法读取 Zotero 连接状态，请刷新页面重试。'
  } finally {
    loading.value = false
  }
}

async function bind() {
  const key = apiKey.value.trim()
  if (!key || saving.value) return
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    connection.value = await api.bindZotero(key)
    apiKey.value = ''
    items.value = []
    searched.value = false
    message.value = 'Zotero 文献库已连接。现在可以在这里或对话中检索你的文献。'
  } catch (e) {
    const detail = String(e)
    error.value = /^Error: (400|403|422):/.test(detail)
      ? '无法连接：请检查密钥是否有效，以及是否有读取个人文献库的权限。'
      : '连接 Zotero 失败，请稍后再试。'
  } finally {
    saving.value = false
  }
}

async function disconnect() {
  if (!connection.value?.connected || saving.value) return
  if (!window.confirm('解除 Zotero 文献库连接？此操作不会删除 Zotero 中的文献。')) return
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    connection.value = await api.disconnectZotero()
    apiKey.value = ''
    query.value = ''
    items.value = []
    searched.value = false
    message.value = '已解除连接。你可以随时重新绑定。'
  } catch {
    error.value = '解除连接失败，请稍后重试。'
  } finally {
    saving.value = false
  }
}

async function search() {
  const q = query.value.trim()
  if (!connection.value?.connected || !q || searching.value) return
  searching.value = true
  error.value = ''
  message.value = ''
  items.value = []
  searched.value = false
  try {
    const result = await api.searchZotero(q)
    items.value = result.items || []
    searched.value = true
  } catch {
    error.value = '暂时无法检索 Zotero 文献。请检查连接状态并稍后重试。'
  } finally {
    searching.value = false
  }
}

function authorsOf(item: ZoteroItem): string {
  return Array.isArray(item.authors) ? item.authors.join('、') : item.authors || ''
}

function safeUrl(value: string | undefined): string {
  if (!value) return ''
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : ''
  } catch {
    return ''
  }
}

function doiUrl(doi: string | undefined): string {
  if (!doi) return ''
  const clean = doi.trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '')
  return clean ? `https://doi.org/${encodeURIComponent(clean)}` : ''
}

onMounted(loadConnection)
</script>

<template>
  <div class="zotero-page">
    <LoadingBar :active="loading || saving || searching" />
    <div v-if="error" class="alert error" role="alert">{{ error }}</div>
    <div v-if="message" class="alert success" role="status">{{ message }}</div>

    <section class="card" aria-labelledby="zotero-connect-title">
      <h3 id="zotero-connect-title">连接你的 Zotero 文献库</h3>
      <p class="intro">连接后，你可以在这里检索自己的文献，也可以让对话助手查找文献。文献仍保存在 Zotero 中。</p>

      <div v-if="connection" class="connection-state">
        <span class="tag" :class="connection.connected ? 'ok' : 'warn'">
          {{ connection.connected ? '已连接' : '未连接' }}
        </span>
        <span v-if="connection.connected && connection.zotero_user_id" class="muted-text">
          账号 {{ connection.zotero_user_id }}
        </span>
      </div>

      <form class="connect-form" @submit.prevent="bind">
        <div class="field">
          <label for="zotero-key">{{ connection?.connected ? '更换 Zotero API 密钥' : 'Zotero API 密钥' }}</label>
          <input
            id="zotero-key"
            v-model="apiKey"
            type="password"
            name="zotero-api-key"
            autocomplete="off"
            spellcheck="false"
            placeholder="粘贴有个人文献库读取权限的密钥"
            :disabled="saving || loading"
          />
        </div>
        <div class="action-row">
          <button type="submit" :disabled="!apiKey.trim() || saving || loading">
            {{ saving ? '正在连接…' : connection?.connected ? '更换连接' : '连接文献库' }}
          </button>
          <button
            v-if="connection?.connected"
            type="button"
            class="secondary"
            :disabled="saving"
            @click="disconnect"
          >解除连接</button>
        </div>
      </form>
      <p class="hint">请在 Zotero 账号设置中创建只有读取权限的 API 密钥。提交后页面不会回显或保存密钥。</p>
    </section>

    <section v-if="connection?.connected" class="card" aria-labelledby="zotero-search-title">
      <h3 id="zotero-search-title">检索文献</h3>
      <div class="field">
        <label for="zotero-query">关键词</label>
        <div class="search-row">
          <input
            id="zotero-query"
            v-model="query"
            placeholder="输入论文标题、作者或主题"
            :disabled="searching"
            @keyup.enter="search"
          />
          <button type="button" :disabled="!query.trim() || searching" @click="search">
            {{ searching ? '检索中…' : '检索' }}
          </button>
        </div>
      </div>
      <p class="hint">在对话中也可以说“从我的 Zotero 文献库里找……”</p>
    </section>

    <section v-if="searched && connection?.connected" class="card" aria-live="polite">
      <h3>检索结果 <span class="tag">{{ items.length }} 篇</span></h3>
      <EmptyState v-if="!items.length" text="没有找到相关文献。试试更短的关键词。" />
      <div v-else class="list">
        <article v-for="(item, index) in items" :key="`${item.zotero_key}-${index}`" class="list-item reference">
          <h4>{{ item.title || '未命名文献' }}</h4>
          <p v-if="authorsOf(item) || item.year || item.venue" class="reference-meta">
            {{ [authorsOf(item), item.year, item.venue].filter(Boolean).join(' · ') }}
          </p>
          <div class="reference-links">
            <a v-if="safeUrl(item.url)" :href="safeUrl(item.url)" target="_blank" rel="noopener noreferrer">查看文献</a>
            <a v-if="doiUrl(item.doi)" :href="doiUrl(item.doi)" target="_blank" rel="noopener noreferrer">DOI: {{ item.doi }}</a>
          </div>
          <details v-if="item.abstract" class="abstract">
            <summary>查看摘要</summary>
            <p>{{ item.abstract }}</p>
          </details>
          <div v-if="item.tags?.length" class="tags">
            <span v-for="tag in item.tags" :key="tag" class="tag">{{ tag }}</span>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.zotero-page {
  max-width: 920px;
}

.intro,
.hint {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.intro {
  margin: -4px 0 16px;
}

.hint {
  margin: 12px 0 0;
}

.connection-state,
.action-row,
.search-row,
.reference-links,
.tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.connection-state {
  margin-bottom: 18px;
}

.connect-form .field {
  max-width: 620px;
}

.search-row input {
  flex: 1;
  min-width: 220px;
}

.reference h4 {
  margin: 0 0 6px;
  font-size: 15px;
  line-height: 1.5;
}

.reference-meta {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
}

.reference-links a {
  color: var(--brand-ink);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.abstract {
  margin-top: 10px;
}

.abstract p {
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.tags {
  margin-top: 10px;
  gap: 4px;
}

@media (max-width: 600px) {
  .search-row {
    align-items: stretch;
  }

  .search-row input,
  .search-row button {
    width: 100%;
  }
}
</style>
