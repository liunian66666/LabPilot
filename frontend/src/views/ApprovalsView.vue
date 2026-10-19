<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { api, type ApprovalInfo } from '../api/client'
import { getUsername, storageKey } from '../utils/auth'
import LoadingBar from '../components/LoadingBar.vue'

const toEmail = ref('')
const subject = ref('')
const body = ref('')
const approval = ref<ApprovalInfo | null>(null)
const detail = ref<Record<string, unknown> | null>(null)
const msg = ref('')
const error = ref('')
const loading = ref(false)
const owner = getUsername()
const key = storageKey('approval_page_id')
let alive = true
let revision = 0
onBeforeUnmount(() => { alive = false })
const draftChanged = computed(() => !!approval.value?.payload && (
  toEmail.value !== approval.value.payload.to_email || subject.value !== approval.value.payload.subject || body.value !== approval.value.payload.body
))
const canDecide = computed(() => !loading.value && approval.value?.status === 'pending' && !draftChanged.value)
const labels: Record<string, string> = { pending: '待确认', deciding: '正在提交', approved: '已批准', sending: '发送中', sent: '已发送', rejected: '已拒绝', failed: '发送失败', unknown: '结果待核查，勿重复发送', expired: '已失效', unavailable: '状态未核实' }
const statusLabel = computed(() => labels[approval.value?.status || ''] || '状态待核对')

async function refreshStatus(id: string, restoreForm = false) {
  const request = ++revision
  try {
    const info = await api.getApproval(id)
    if (!alive || getUsername() !== owner || request !== revision) return
    approval.value = { ...info, approval_id: info.id || id }
    detail.value = info as unknown as Record<string, unknown>
    if (restoreForm && info.payload) {
      toEmail.value = info.payload.to_email || ''
      subject.value = info.payload.subject || ''
      body.value = info.payload.body || ''
    }
  } catch {
    if (alive && getUsername() === owner && request === revision) {
      if (approval.value) approval.value.status = 'unavailable'
      error.value = '邮件状态核对失败，请刷新状态后再操作。'
    }
  }
}

onMounted(async () => {
  const id = localStorage.getItem(key)
  if (!id) return
  loading.value = true
  approval.value = { approval_id: id, status: 'unavailable', payload_version: '' }
  await refreshStatus(id, true)
  loading.value = false
})

async function prepare() {
  if (loading.value || !toEmail.value.trim() || !subject.value.trim() || !body.value.trim()) return
  loading.value = true
  error.value = ''
  msg.value = ''
  try {
    const proposed = await api.prepareEmail({ to_email: toEmail.value, subject: subject.value, body: body.value, session_id: 'ui' })
    if (!alive || getUsername() !== owner) return
    const id = proposed.approval_id || proposed.id
    if (!id) throw new Error('缺少审批编号')
    approval.value = { ...proposed, approval_id: id, status: 'unavailable' }
    localStorage.setItem(key, id)
    await refreshStatus(id)
    msg.value = approval.value?.status === 'pending' ? '请核对下面的邮件内容，再确认发送。' : '已恢复同内容邮件的处理状态，没有新建或重复发送。'
  } catch (e) {
    if (alive && getUsername() === owner) error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function decide(decision: 'approved' | 'rejected') {
  if (!canDecide.value || !approval.value?.approval_id) return
  const id = approval.value.approval_id
  const version = approval.value.payload_version
  revision++
  loading.value = true
  approval.value.status = 'deciding'
  error.value = ''
  try {
    await api.decide({ approval_id: id, decision, payload_version: version, session_id: 'ui' })
  } catch (e) {
    if (alive && getUsername() === owner) error.value = String(e)
  } finally {
    await refreshStatus(id)
    loading.value = false
  }
}

async function refresh() {
  if (loading.value || !approval.value?.approval_id) return
  loading.value = true
  await refreshStatus(approval.value.approval_id)
  loading.value = false
}

async function editAndReprepare() {
  if (loading.value) return
  await prepare()
}
</script>

<template>
  <div>
    <LoadingBar :active="loading" />
    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="msg" class="alert info">{{ msg }}</div>

    <div class="steps">
      <span class="step" :class="{ on: true }">1 草稿</span>
      <span class="step-line"></span>
      <span class="step" :class="{ on: !!approval }">2 确认</span>
      <span class="step-line"></span>
      <span class="step" :class="{ on: !!detail }">3 结果</span>
    </div>

    <!-- 1. 草稿 -->
    <div class="card">
      <h3>准备邮件草稿</h3>
      <div class="grid-2">
        <div class="field">
          <label>收件人</label>
          <input v-model="toEmail" type="email" />
        </div>
        <div class="field">
          <label>主题</label>
          <input v-model="subject" />
        </div>
      </div>
      <div class="field">
        <label>正文</label>
        <textarea v-model="body" rows="5" />
      </div>
      <div class="row">
        <button @click="prepare" :disabled="loading">生成审批单</button>
        <button class="secondary" @click="editAndReprepare" :disabled="loading">
          改稿并重新申请
        </button>
      </div>
    </div>

    <!-- 2. 人工确认 -->
    <div class="card" v-if="approval">
      <h3>
        人工确认（HITL）
        <span class="tag mono">approval {{ (approval.approval_id || '').slice(0, 8) }}</span>
        <span class="tag mono">ver {{ approval.payload_version.slice(0, 8) }}</span>
        <span class="tag" :class="approval.status === 'pending' ? 'warn' : 'ok'">
          {{ statusLabel }}
        </span>
      </h3>

      <div class="mail-preview">
        <div class="mail-row">
          <span class="mail-key">收件人</span>
          <span class="mail-val mono">{{ approval.payload?.to_email }}</span>
        </div>
        <div class="mail-row">
          <span class="mail-key">主题</span>
          <span class="mail-val">{{ approval.payload?.subject }}</span>
        </div>
        <div class="mail-body">{{ approval.payload?.body }}</div>
      </div>

      <p class="confirm-hint">
        批准后按上述内容原样外发；内容版本已绑定（
        <span class="mono">{{ approval.payload_version.slice(0, 8) }}</span> ），改稿需重新申请。
      </p>

      <p v-if="draftChanged" class="confirm-hint">草稿已修改，请重新生成审批单后再确认。</p>
      <div class="row" style="margin-top: 12px">
        <button
          class="danger"
          @click="decide('rejected')"
          :disabled="!canDecide"
        >
          拒绝（不外发）
        </button>
        <button
          @click="decide('approved')"
          :disabled="!canDecide"
        >
          {{ loading ? '处理中…' : approval.status === 'sent' ? '已发送' : '批准并发送' }}
        </button>
        <button class="secondary" :disabled="loading" @click="refresh">刷新状态</button>
      </div>
    </div>

    <!-- 3. 审批单状态 -->
    <div class="card" v-if="detail">
      <h3>审批单状态</h3>
      <pre class="report mono">{{ JSON.stringify(detail, null, 2) }}</pre>
    </div>
  </div>
</template>

<style scoped>
/* 步骤导航 */
.steps {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 18px;
}

.step {
  padding: 5px 14px;
  border-radius: 999px;
  font-size: 13px;
  color: var(--muted);
  background: rgba(255, 255, 255, 0.75);
  box-shadow: inset 0 0 0 1px var(--line-strong);
}

.step.on {
  background: var(--brand-soft);
  color: var(--brand-ink);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(47, 95, 208, 0.3);
}

.step-line {
  flex: none;
  width: 26px;
  height: 1px;
  background: var(--line-strong);
}

/* 邮件预览（信封式） */
.mail-preview {
  border: 1px solid var(--line);
  border-radius: 12px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.9);
}

.mail-row {
  display: flex;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  background: rgba(247, 249, 253, 0.8);
}

.mail-key {
  flex: none;
  width: 52px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.7;
}

.mail-val {
  font-size: 14px;
  color: var(--ink);
}

.mail-body {
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--ink);
  white-space: pre-wrap;
  max-height: 260px;
  overflow: auto;
}

.confirm-hint {
  margin: 10px 2px 0;
  font-size: 12.5px;
  color: var(--muted);
}
</style>
