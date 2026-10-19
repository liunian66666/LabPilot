const exampleCandidates = [
  { id: 'exp', title: '补完消融实验并整理结果', due: '本周', view: 'week', quote: '下周五前补完消融实验，并把结果整理给老师看。' },
  { id: 'papers', title: '核对 20 篇文献的年份与 DOI', due: '本周', view: 'week', quote: '小王负责核对 20 篇相关文献的年份与 DOI，完成后更新综述表。' },
  { id: 'failures', title: '整理失败样例与可能原因', due: '日期待确认', view: 'waiting', quote: '把上周实验的失败样例整理成一页记录，说明可能原因，具体截止时间下次确认。' }
];
const initialState = () => ({ extracted: false, todos: [], progress: [], report: '', reportNumber: 0, approval: null, view: 'all' });
let state = initialState();
const labels = { overview: '预览导览', meeting: '纪要抽任务', todos: '四维待办', progress: '进展记录', report: '周报草稿', approval: '发送确认', knowledge: '资料库问答' };
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
let toastTimer;
function toast(message) { const el = $('toast'); el.textContent = message; el.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 2600); }
function show(screen) {
  document.querySelectorAll('.screen').forEach(el => el.classList.toggle('active', el.id === `screen-${screen}`));
  document.querySelectorAll('.nav-item').forEach(el => { const active = el.dataset.screen === screen; el.classList.toggle('selected', active); if (active) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current'); });
  $('breadcrumb').textContent = labels[screen];
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
document.querySelectorAll('[data-screen]').forEach(button => button.addEventListener('click', () => show(button.dataset.screen)));
document.querySelectorAll('[data-go]').forEach(button => button.addEventListener('click', () => show(button.dataset.go)));

function renderCandidates() {
  $('candidate-count').textContent = state.extracted ? '3 条候选' : '未提取';
  if (!state.extracted) { $('candidates').className = 'empty-state'; $('candidates').innerHTML = '<div>✳</div><h3>等待提取</h3><p>点击左侧按钮，查看任务、来源句与待确认字段。</p>'; return; }
  $('candidates').className = '';
  $('candidates').innerHTML = exampleCandidates.map(item => `<div class="candidate"><div class="candidate-top"><strong>${escapeHtml(item.title)}</strong><span class="due">${escapeHtml(item.due)}</span></div><blockquote>${escapeHtml(item.quote)}</blockquote><button data-add="${item.id}" ${state.todos.some(todo => todo.id === item.id) ? 'disabled' : ''}>${state.todos.some(todo => todo.id === item.id) ? '已加入待办 ✓' : '加入待办 →'}</button></div>`).join('');
  document.querySelectorAll('[data-add]').forEach(button => button.addEventListener('click', () => addTodo(button.dataset.add)));
}
function addTodo(id) {
  if (state.todos.some(todo => todo.id === id)) return;
  const item = exampleCandidates.find(candidate => candidate.id === id);
  if (!item) return;
  state.todos.push({ ...item, done: false });
  renderCandidates(); renderTodos(); renderProgress(); toast('已加入待办：' + item.title);
}
$('extract-btn').addEventListener('click', () => { state.extracted = true; renderCandidates(); toast('已展示固定示例任务与原文依据'); });

function renderTodos() {
  $('todo-total').textContent = state.todos.length;
  $('todo-done').textContent = state.todos.filter(item => item.done).length;
  $('todo-waiting').textContent = state.todos.filter(item => item.view === 'waiting').length;
  const items = state.todos.filter(item => state.view === 'all' || item.view === state.view);
  $('todo-list').innerHTML = items.length ? items.map(item => `<label class="todo-item ${item.done ? 'done' : ''}"><input type="checkbox" data-todo="${item.id}" ${item.done ? 'checked' : ''}><div><strong>${escapeHtml(item.title)}</strong><p>来源：${escapeHtml(item.quote)}</p></div><span class="todo-tag">${escapeHtml(item.due)}</span></label>`).join('') : `<div class="empty-state"><div>☷</div><h3>${state.todos.length ? '这个视图暂时没有任务' : '还没有待办'}</h3><p>${state.todos.length ? '可切换到“全部”查看已加入的任务。' : '先到“纪要抽任务”提取并加入任务。'}</p></div>`;
  document.querySelectorAll('[data-todo]').forEach(input => input.addEventListener('change', () => { const todo = state.todos.find(item => item.id === input.dataset.todo); todo.done = input.checked; renderTodos(); }));
}
document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => { state.view = button.dataset.view; document.querySelectorAll('.tab').forEach(el => el.classList.toggle('current', el === button)); renderTodos(); }));

function renderProgress() {
  $('progress-task').innerHTML = state.todos.length ? state.todos.map(todo => `<option value="${todo.id}">${escapeHtml(todo.title)}</option>`).join('') : '<option value="">请先添加待办</option>';
  $('progress-count').textContent = `${state.progress.length} 条`;
  $('progress-list').innerHTML = state.progress.length ? [...state.progress].reverse().map(entry => `<div class="progress-entry"><strong>${escapeHtml(entry.title)}</strong><p>${escapeHtml(entry.text)}</p></div>`).join('') : '<div class="empty-state"><div>▥</div><h3>还没有进展记录</h3><p>记录真实做过的工作，周报才有依据。</p></div>';
}
$('progress-form').addEventListener('submit', event => { event.preventDefault(); const id = $('progress-task').value; const text = $('progress-text').value.trim(); const task = state.todos.find(item => item.id === id); if (!task || !text) { toast('请先加入待办并填写进展'); return; } state.progress.push({ taskId: id, title: task.title, text }); $('progress-text').value = ''; renderProgress(); toast('进展已记录，可继续生成周报'); });

function renderReport() {
  $('report-text').value = state.report;
  $('report-version').textContent = state.reportNumber ? `草稿 v${state.reportNumber}` : '尚未生成';
  $('report-sources').textContent = state.progress.length ? state.progress.map((entry, index) => `进展 ${index + 1}：${entry.title}`).join(' · ') : '暂无进展记录；未完成部分会标为“待补充”';
}
$('generate-report').addEventListener('click', () => {
  const finished = state.progress.map((entry, index) => `${index + 1}. ${entry.title}：${entry.text}`);
  const missing = state.todos.filter(todo => !state.progress.some(entry => entry.taskId === todo.id)).map(todo => `- ${todo.title}：待补充实际进展`);
  state.reportNumber += 1;
  state.report = `# 本周周报草稿\n\n## 已记录进展\n${finished.length ? finished.join('\n') : '待补充：本周尚无进展记录。'}\n\n## 待补充事项\n${missing.length ? missing.join('\n') : '暂无。'}\n\n## 下周计划\n根据待办清单继续推进，具体安排待确认。`;
  state.approval = null; renderReport(); renderApproval(); toast('草稿已生成；请核对并编辑内容');
});
$('report-text').addEventListener('input', () => { state.report = $('report-text').value; if (state.approval && state.approval.body !== state.report) { state.approval = null; renderApproval(); toast('正文已修改，原确认失效'); } });
$('prepare-email').addEventListener('click', () => { if (!state.report.trim()) { toast('请先生成或填写周报'); return; } state.approval = { body: state.report, version: state.reportNumber, status: 'pending' }; renderApproval(); show('approval'); toast('已创建本地示例确认'); });

function renderApproval() {
  const approval = state.approval;
  $('approval-content').textContent = approval ? approval.body : '请先生成周报，并点击“准备发送确认”。';
  $('approval-content').classList.toggle('muted', !approval);
  $('approve-btn').disabled = !approval || approval.status !== 'pending';
  $('reject-btn').disabled = !approval || approval.status !== 'pending';
  const status = $('approval-status');
  status.className = 'status-box' + (approval?.status === 'approved' ? ' success' : approval?.status === 'rejected' ? ' warn' : '');
  const details = !approval ? ['○', '尚未提交确认', '完成周报后可在这里体验审批状态流转。'] : approval.status === 'approved' ? ['✓', '已模拟确认', '这里只更新演示状态，没有发送任何邮件。'] : approval.status === 'rejected' ? ['×', '已拒绝', '该确认已结束；修改草稿后可重新准备。'] : ['●', '等待人工确认', `待确认草稿 v${approval.version || 1}；请核对完整正文。`];
  status.innerHTML = `<span>${details[0]}</span><div><strong>${details[1]}</strong><p>${details[2]}</p></div>`;
}
$('approve-btn').addEventListener('click', () => { if (!state.approval || state.approval.status !== 'pending') return; state.approval.status = 'approved'; renderApproval(); toast('已模拟确认；未发送真实邮件'); });
$('reject-btn').addEventListener('click', () => { if (!state.approval || state.approval.status !== 'pending') return; state.approval.status = 'rejected'; renderApproval(); toast('已拒绝本次示例确认'); });

document.querySelectorAll('[data-answer]').forEach(button => button.addEventListener('click', () => { $('knowledge-answer').className = 'answer'; $('knowledge-answer').innerHTML = button.dataset.answer === '1' ? '<h3>有依据的回答</h3><p>根据示例组会纪要，下一步是补完消融实验，并整理结果给老师查看。这里没有记录实验结果，因此不会推断结果好坏。</p><div class="source-quote">来源：组会纪要 · “下周五前补完消融实验，并把结果整理给老师看。”</div>' : '<h3>资料中没有确认信息</h3><p>当前示例纪要只要求把结果整理给老师看，没有记录老师已经确认最终图表。需要查看后续反馈记录才能回答。</p><div class="source-quote">依据边界：示例组会纪要未包含最终确认记录。</div>'; }));
$('reset-demo').addEventListener('click', () => { state = initialState(); renderCandidates(); renderTodos(); renderProgress(); renderReport(); renderApproval(); $('knowledge-answer').className = 'empty-state'; $('knowledge-answer').innerHTML = '<div>◇</div><h3>选择一个问题</h3><p>有来源时展示对应片段；没有依据时明确说明未知。</p>'; document.querySelectorAll('.tab').forEach(el => el.classList.toggle('current', el.dataset.view === 'all')); show('overview'); toast('演示已重置'); });
renderCandidates(); renderTodos(); renderProgress(); renderReport(); renderApproval();
