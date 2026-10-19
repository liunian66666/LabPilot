<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type ExtractedTask, type Priority, type TodoSource } from '../api/client'
import LoadingBar from '../components/LoadingBar.vue'
import EmptyState from '../components/EmptyState.vue'

const text = ref('组会纪要：下周五前补完消融实验，结果给老师看一下。今天提交开题修改稿。')
const candidates = ref<Candidate[]>([])
const docId = ref('')
const loading = ref(false)
const creating = ref(false)
const error = ref('')
const msg = ref('')

/** 可编辑候选：抽取结果 + 前端编辑态。 */
interface Candidate {
  include: boolean
  title: string
  detail: string
  due_raw: string
  priority: Priority
  source: TodoSource
  deliverable: string
  source_quote: string
  needs_confirm: boolean
}

const priorityLabels: Record<string, string> = {
  urgent_important: '重要且紧急',
  important: '重要不紧急',
  urgent: '紧急不重要',
  normal: '普通',
}

const sourceLabels: Record<string, string> = {
  meeting: '组会',
  advisor: '导师',
  paper_ddl: '论文 DDL',
  personal: '个人',
  comm: '沟通',
}

const includedCount = computed(() => candidates.value.filter((c) => c.include).length)

/** 第一步：只抽取候选，不落库（persist=false）。 */
async function run() {
  loading.value = true
  error.value = ''
  msg.value = ''
  candidates.value = []
  try {
    const res = await api.extract(text.value, false)
    candidates.value = res.tasks.map((t) => ({
      include: true,
      title: t.title,
      detail: t.detail === t.title ? '' : t.detail,
      due_raw: t.due_raw || '',
      priority: t.priority,
      source: t.source,
      deliverable: t.deliverable || '',
      source_quote: t.source_quote || '',
      needs_confirm: t.needs_confirm,
    }))
    docId.value = res.document_id || ''
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

/** 第二步：用户确认后逐条入库，保留证据链。 */
async function confirmAll() {
  const picked = candidates.value.filter((c) => c.include && c.title.trim())
  if (!picked.length) return
  creating.value = true
  error.value = ''
  let ok = 0
  try {
    for (const c of picked) {
      const detail = [c.detail, c.deliverable ? `交付物：${c.deliverable}` : '']
        .filter(Boolean)
        .join('\n')
      await api.createTodo({
        title: c.title.trim(),
        detail,
        due_raw: c.due_raw.trim(),
        priority: c.priority,
        source: c.source,
        source_quote: c.source_quote,
        origin_document_id: docId.value || undefined,
      })
      ok += 1
    }
    msg.value = `已确认入库 ${ok} 条待办（含原文依据），可在「四维待办」中查看。`
    candidates.value = []
  } catch (e) {
    error.value = `入库中断（已成功 ${ok} 条）：${e}`
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div>
    <LoadingBar :active="loading || creating" />
    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="msg" class="alert success">{{ msg }}</div>

    <div class="extract-grid">
      <!-- 左：原文输入 -->
      <div class="card">
        <h3>粘贴组会纪要 / 任务句</h3>
        <div class="field">
          <label>原文</label>
          <textarea v-model="text" rows="12" />
        </div>
        <div class="row">
          <button @click="run" :disabled="loading || creating || !text.trim()">
            {{ loading ? '模型解析中' : candidates.length ? '重新抽取' : '抽取候选' }}
          </button>
        </div>
        <div v-if="docId" class="alert info" style="margin-bottom: 0">
          关联文档：<span class="mono">{{ docId.slice(0, 8) }}</span>
        </div>
        <p class="hint">
          抽取只生成候选清单，<strong>不会直接写入待办库</strong>；你在右侧核对、修改、剔除后，
          点「确认入库」才创建，每条保留原文依据（quote）。
        </p>
      </div>

      <!-- 右：候选确认 -->
      <div class="card result-card">
        <h3>
          候选确认
          <span v-if="candidates.length" class="tag" :class="includedCount ? 'ok' : 'warn'">
            {{ includedCount }} / {{ candidates.length }} 待入库
          </span>
        </h3>
        <EmptyState
          v-if="!candidates.length"
          text="尚无候选。粘贴纪要后点击「抽取候选」，确认无误再入库。"
        />
        <div class="list">
          <div class="task-item" v-for="(c, i) in candidates" :key="i" :class="{ off: !c.include }">
            <div class="task-head">
              <label class="pick">
                <input type="checkbox" v-model="c.include" />
                <span class="task-no">{{ i + 1 }}</span>
              </label>
              <input class="title-input" v-model="c.title" placeholder="任务标题" />
            </div>
            <div class="edit-grid">
              <label class="mini">
                <span>日期/截止</span>
                <input v-model="c.due_raw" placeholder="如：下周五前 / 2026-10-10" />
              </label>
              <label class="mini">
                <span>优先级</span>
                <select v-model="c.priority">
                  <option value="urgent_important">重要且紧急</option>
                  <option value="important">重要不紧急</option>
                  <option value="urgent">紧急不重要</option>
                  <option value="normal">普通</option>
                </select>
              </label>
              <label class="mini">
                <span>来源</span>
                <select v-model="c.source">
                  <option value="meeting">组会</option>
                  <option value="advisor">导师</option>
                  <option value="paper_ddl">论文 DDL</option>
                  <option value="personal">个人</option>
                  <option value="comm">沟通</option>
                </select>
              </label>
              <label class="mini">
                <span>交付物</span>
                <input v-model="c.deliverable" placeholder="如：实验报告" />
              </label>
            </div>
            <div v-if="c.detail" class="task-detail">{{ c.detail }}</div>
            <div class="quote" v-if="c.source_quote">
              <span class="quote-label">原文依据</span>
              {{ c.source_quote }}
            </div>
          </div>
        </div>
        <div class="row" v-if="candidates.length" style="margin-top: 14px">
          <button @click="confirmAll" :disabled="creating || !includedCount">
            {{ creating ? '入库中…' : `确认入库（${includedCount} 条）` }}
          </button>
          <button class="secondary" @click="candidates = []" :disabled="creating">放弃</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.extract-grid {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
  gap: 18px;
  align-items: start;
}

.result-card {
  min-height: 320px;
}

.hint {
  margin: 14px 0 0;
  font-size: 12.5px;
  color: var(--muted);
  line-height: 1.6;
}

.task-item {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 13px 15px;
  background: rgba(255, 255, 255, 0.75);
  transition: border-color 0.15s ease, opacity 0.15s ease;
}

.task-item.off {
  opacity: 0.45;
}

.task-item:hover {
  border-color: var(--line-strong);
}

.task-head {
  display: flex;
  align-items: center;
  gap: 9px;
}

.pick {
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
}

.pick input {
  width: 15px;
  height: 15px;
  margin: 0;
}

.task-no {
  flex: none;
  width: 21px;
  height: 21px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  background: var(--brand-soft);
  color: var(--brand-ink);
  font-size: 12px;
  font-weight: 600;
}

.title-input {
  flex: 1;
  font-weight: 600;
}

.edit-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 12px;
  margin: 10px 0 0 37px;
}

.mini {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11.5px;
  color: var(--muted);
}

.mini input,
.mini select {
  font-size: 13px;
  padding: 6px 9px;
}

.task-detail {
  margin: 8px 0 0 37px;
  font-size: 13px;
  color: var(--ink-soft);
  line-height: 1.6;
}

.quote {
  margin: 8px 0 0 37px;
  font-size: 12.5px;
  color: var(--muted);
}

.quote-label {
  display: inline-block;
  margin-right: 6px;
  padding: 0 6px;
  border-radius: 5px;
  background: rgba(47, 95, 208, 0.1);
  color: var(--brand-ink);
  font-size: 11px;
}

@media (max-width: 1100px) {
  .extract-grid {
    grid-template-columns: 1fr;
  }
}
</style>
