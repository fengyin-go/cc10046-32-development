<template>
  <section class="dispatch-panel" data-testid="dispatch-panel" data-module="crawl_schedule_claim">
    <div class="dp-head">
      <h3>来源分片计划 · 领取台</h3>
      <p class="dp-tip">
        调度批次凭据持久保存<strong>计划版本、分页范围、实际批次</strong>；建批响应中断后，
        用<strong>同一申请</strong>续领即返回原批次，不会重复建批。
      </p>
      <div class="dp-head-actions">
        <button class="btn" data-testid="load-scenario" type="button" @click="onLoadScenario">
          载入题设场景（旧 1-5 / 新 4-7）
        </button>
        <button class="btn ghost" data-testid="refresh-board" type="button" @click="refreshAll">刷新核对</button>
      </div>
      <p v-if="message" class="dp-message" :class="{ error: isError }" data-testid="dp-message">{{ message }}</p>
    </div>

    <!-- 核对看板 -->
    <div class="board-grid" data-testid="claim-board">
      <article class="board-card new">
        <span class="board-label">已经发给新批的页码</span>
        <strong class="pages" data-testid="new-batch-pages">{{ formatPages(board.new_batch_pages) }}</strong>
        <span class="board-sub">新计划 {{ latestVersion }} 实际领取范围</span>
      </article>
      <article class="board-card pending">
        <span class="board-label">还在等旧领取核清的页码</span>
        <strong class="pages" data-testid="old-pending-pages">{{ formatPages(board.old_pending_pages) }}</strong>
        <span class="board-sub">旧计划已建批但未拿到领取回执</span>
      </article>
      <article class="board-card overlap">
        <span class="board-label">新旧范围重叠页码</span>
        <strong class="pages" data-testid="overlap-pages">{{ formatPages(board.overlap_pages) }}</strong>
        <span class="board-sub">重叠页以凭据快照范围为准分别追踪</span>
      </article>
    </div>

    <div class="dp-grid">
      <!-- 左：计划确认 + 领取 -->
      <div class="dp-col">
        <form class="dp-card" data-testid="plan-form" @submit.prevent="onConfirmPlan">
          <h4>确认来源分片计划（生成新版本）</h4>
          <label class="dp-field">
            <span>来源标识</span>
            <input v-model="planForm.source" data-testid="plan-source" />
          </label>
          <div class="dp-row">
            <label class="dp-field">
              <span>起始页</span>
              <input v-model.number="planForm.page_start" type="number" min="1" data-testid="page-start" />
            </label>
            <label class="dp-field">
              <span>结束页</span>
              <input v-model.number="planForm.page_end" type="number" min="1" data-testid="page-end" />
            </label>
            <label class="dp-field">
              <span>每片页数</span>
              <input v-model.number="planForm.shard_size" type="number" min="1" data-testid="shard-size" />
            </label>
            <label class="dp-field">
              <span>版本(可空)</span>
              <input v-model="planForm.version" data-testid="plan-version" placeholder="自增" />
            </label>
          </div>
          <button class="btn primary" data-testid="confirm-plan" type="submit" :disabled="busy">确认分片计划</button>
        </form>

        <form class="dp-card" data-testid="claim-form" @submit.prevent="onClaim">
          <h4>领取分片 · 生成调度批次凭据</h4>
          <label class="dp-field">
            <span>同一申请单号（幂等键）</span>
            <input v-model="claimForm.application_id" data-testid="claim-application" placeholder="例如 APP-NEW-1" />
          </label>
          <div class="dp-row">
            <label class="dp-field grow">
              <span>计划版本</span>
              <select v-model="claimForm.plan_id" data-testid="claim-plan">
                <option value="">最新版本</option>
                <option v-for="p in plans" :key="p.plan_id" :value="p.plan_id">
                  {{ p.计划版本 }}（{{ p.page_range }} 页）
                </option>
              </select>
            </label>
            <label class="dp-field small">
              <span>第几片(可空)</span>
              <input v-model.number="claimForm.shard_index" type="number" min="1" data-testid="claim-shard" placeholder="下一片" />
            </label>
            <label class="dp-field grow">
              <span>实际批次号(可空)</span>
              <input v-model="claimForm.batch_id" data-testid="claim-batch" placeholder="自动生成" />
            </label>
          </div>
          <label class="dp-check">
            <input v-model="claimForm.disconnect" type="checkbox" data-testid="claim-interrupt" />
            建批成功后中断调度确认响应（模拟回执丢失）
          </label>
          <div class="dp-btn-row">
            <button class="btn primary" data-testid="claim-submit" type="submit" :disabled="busy">领取</button>
            <button class="btn ghost" type="button" data-testid="gen-application" @click="newApplication">生成申请号</button>
          </div>
        </form>

        <form class="dp-card resume" data-testid="resume-form" @submit.prevent="onResume">
          <h4>断点续做 · 续领同一申请</h4>
          <div class="dp-row">
            <label class="dp-field grow">
              <span>原申请单号 / 凭据号 / 批次号</span>
              <input v-model="resumeKey" data-testid="resume-application" placeholder="APP-OLD-SHARD2" />
            </label>
            <button class="btn primary" data-testid="resume-submit" type="submit" :disabled="busy">续领</button>
            <button class="btn" data-testid="confirm-receipt" type="button" :disabled="busy" @click="onConfirm">确认回执</button>
          </div>
          <p v-if="lastCredential" class="replay-hint" data-testid="replay-hint">
            最近返回：凭据 <b>{{ lastCredential.credential_id }}</b> ·
            原批次 <b data-testid="last-batch-id">{{ lastCredential.batch_id }}</b> ·
            版本 <b>{{ lastCredential.计划版本 }}</b> ·
            范围 <b>{{ lastCredential.分页范围 }}</b>
            <span v-if="lastCredential.replayed" class="replay-tag" data-testid="replayed-tag">同一申请，返回原批次，未重复建批</span>
          </p>
        </form>
      </div>

      <!-- 右：计划版本 + 凭据 + 历史 -->
      <div class="dp-col">
        <div class="dp-card" data-testid="plan-list">
          <h4>两个计划独立查看 · 实际执行范围</h4>
          <article v-for="p in board.plans || []" :key="p.plan_id" class="plan-card" data-testid="plan-card">
            <header class="plan-head">
              <span class="plan-version" data-testid="plan-version-label">{{ p.计划版本 }}</span>
              <span class="plan-range" data-testid="plan-range">{{ p.page_range }} 页</span>
              <span class="plan-pages">全部页码：{{ formatPages(p.pages) }}</span>
            </header>
            <div class="shard-chips">
              <span v-for="s in p.shards" :key="s.shard_index" class="shard-chip"
                    :class="{ claimed: shardClaimed(p, s.shard_index) }"
                    :data-testid="`shard-${p.version}-${s.shard_index}`">
                第{{ s.shard_index }}片：{{ s.page_range }}页
              </span>
            </div>
            <table v-if="p.actual_ranges && p.actual_ranges.length" class="mini-table">
              <thead>
                <tr><th>凭据</th><th>实际批次</th><th>片</th><th>真实领取范围</th><th>回执</th></tr>
              </thead>
              <tbody>
                <tr v-for="r in p.actual_ranges" :key="r.credential_id" :data-testid="`range-row-${r.credential_id}`">
                  <td>{{ r.credential_id }}</td>
                  <td data-testid="actual-batch">{{ r.batch_id }}</td>
                  <td>{{ r.shard_index }}</td>
                  <td data-testid="actual-range">{{ r.page_range }} 页（{{ formatPages(r.pages) }}）</td>
                  <td :class="r.needs_verification ? 'warn' : 'ok'">{{ r.needs_verification ? (r.verification_status || '待核清') : r.status }}</td>
                </tr>
              </tbody>
            </table>
          </article>
          <p v-if="!(board.plans || []).length" class="dp-empty">暂无计划，可先确认计划或载入题设场景。</p>
        </div>

        <div class="dp-card" data-testid="credential-list">
          <h4>调度批次凭据登记 / 领取历史</h4>
          <table v-if="credentials.length" class="data-table cred-table">
            <thead>
              <tr>
                <th>调度批次凭据</th><th>申请单号</th><th>计划版本</th><th>分页范围</th>
                <th>实际批次</th><th>次数</th><th>状态</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in credentials" :key="c.credential_id" :data-testid="`credential-row-${c.credential_id}`">
                <td>{{ c.credential_id }}</td>
                <td>{{ c.application_id }}</td>
                <td data-testid="cred-version">{{ c.计划版本 }}</td>
                <td data-testid="cred-range">{{ c.分页范围 }}（{{ formatPages(c.pages) }}）</td>
                <td data-testid="cred-batch">{{ c.batch_id }}</td>
                <td>{{ c.claim_count }}</td>
                <td :class="c.needs_verification ? 'warn' : 'ok'" data-testid="cred-status">{{ c.needs_verification ? (c.receipt_status || c.verification_status || '待核清') : c.status }}</td>
                <td class="cred-actions">
                  <button class="link" type="button" :data-testid="`row-resume-${c.application_id}`"
                          @click="quickResume(c.application_id)">重试此申请</button>
                  <button class="link" type="button" :data-testid="`row-history-${c.application_id}`"
                          @click="showHistory(c.application_id)">历史</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="dp-empty">暂无调度批次凭据。</p>

          <ol v-if="history.length" class="history-list" data-testid="history-list">
            <li v-for="h in history" :key="h.seq" :data-testid="`history-item-${h.seq}`" :data-kind="h.kind">
              <span class="history-seq">#{{ h.seq }}</span>
              <span class="history-event" data-testid="history-event">{{ h.event }}</span>
              <span class="history-time">{{ h.time }}</span>
            </li>
          </ol>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  claimShard, confirmClaim, confirmPlan, loadBoard, loadCredentials,
  loadHistory, loadScenario, resumeClaim,
  type AnyRecord,
} from '@/api/dispatch'

const SOURCE = 'SRC-PAGE'
const busy = ref(false)
const message = ref('')
const isError = ref(false)

const board = ref<AnyRecord>({})
const credentials = ref<AnyRecord[]>([])
const history = ref<AnyRecord[]>([])
const lastCredential = ref<AnyRecord | null>(null)
const resumeKey = ref('APP-OLD-SHARD2')

const planForm = reactive({
  source: SOURCE, page_start: 1, page_end: 5, shard_size: 2, version: '' as string | number,
})
const claimForm = reactive({
  application_id: '', plan_id: '', shard_index: '' as string | number,
  batch_id: '', disconnect: false, source: SOURCE,
})

const plans = computed<AnyRecord[]>(() => board.value.plans || [])
const latestVersion = computed(() => {
  const list = board.value.plans || []
  return list.length ? list[list.length - 1].计划版本 : '—'
})

function formatPages(pages?: number[]): string {
  if (!pages || !pages.length) return '—'
  return pages.join(', ')
}

function shardClaimed(plan: AnyRecord, shardIndex: number): boolean {
  return (plan.actual_ranges || []).some((r: AnyRecord) => r.shard_index === shardIndex)
}

function flash(text: string, error = false) {
  message.value = text
  isError.value = error
}

async function refreshAll() {
  const source = planForm.source || SOURCE
  try {
    const [b, c] = await Promise.all([loadBoard(source), loadCredentials(source)])
    board.value = b
    credentials.value = c.items || []
    if (!history.value.length) {
      const h = await loadHistory(undefined, source)
      history.value = (h.items || []).slice(-12).reverse()
    }
  } catch (error) {
    flash(error instanceof Error ? error.message : '读取领取看板失败', true)
  }
}

async function onLoadScenario() {
  busy.value = true
  try {
    const result = await loadScenario(SOURCE)
    planForm.source = SOURCE
    claimForm.source = SOURCE
    resumeKey.value = 'APP-OLD-SHARD2'
    flash('题设场景已就绪：旧计划 1-5 页第二片建批中断，新计划 4-7 页已确认。')
    board.value = result.board || {}
    await refreshAll()
    history.value = (await loadHistory('APP-OLD-SHARD2')).items || []
  } catch (error) {
    flash(error instanceof Error ? error.message : '载入场景失败', true)
  } finally {
    busy.value = false
  }
}

async function onConfirmPlan() {
  busy.value = true
  try {
    const body: AnyRecord = { ...planForm }
    if (body.version === '' || body.version === null) delete body.version
    const plan = await confirmPlan(body)
    flash(`已确认 ${plan.计划版本}：第 ${plan.page_range} 页，共 ${plan.shard_count} 片。`)
    await refreshAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '确认计划失败', true)
  } finally {
    busy.value = false
  }
}

function newApplication() {
  claimForm.application_id = `APP-${Date.now().toString(36).toUpperCase()}`
}

async function onClaim() {
  busy.value = true
  try {
    if (!claimForm.application_id) newApplication()
    const body: AnyRecord = {
      application_id: claimForm.application_id,
      source: planForm.source,
      disconnect: claimForm.disconnect,
    }
    if (claimForm.plan_id) body.plan_id = claimForm.plan_id
    if (claimForm.shard_index !== '' && claimForm.shard_index !== null) {
      body.shard_index = Number(claimForm.shard_index)
    }
    if (claimForm.batch_id) body.batch_id = claimForm.batch_id

    let result: AnyRecord
    try {
      result = await claimShard(body)
    } catch (error) {
      // request 封装对 503 不抛，但若网络层拿到的是中断，提示后续续领。
      throw error
    }
    lastCredential.value = result.credential || result
    if (result.interrupted || result.status === 503) {
      flash(`实际批次 ${lastCredential.value?.batch_id} 已创建，但响应中断；请用同一申请 ${claimForm.application_id} 点「续领」。`, true)
      resumeKey.value = claimForm.application_id
    } else {
      flash(`领取成功：凭据 ${lastCredential.value?.credential_id}，实际批次 ${lastCredential.value?.batch_id}。`)
      resumeKey.value = claimForm.application_id
    }
    await refreshAll()
    await showHistory(claimForm.application_id)
  } catch (error) {
    flash(error instanceof Error ? error.message : '领取失败', true)
  } finally {
    busy.value = false
  }
}

async function onResume() {
  if (!resumeKey.value) return
  busy.value = true
  try {
    const result = await resumeClaim(resumeKey.value.trim())
    lastCredential.value = result.credential || result
    flash(`续领完成：返回原批次 ${lastCredential.value?.batch_id}，未重复建批；首次领取历史保留。`)
    await refreshAll()
    const appId = lastCredential.value?.application_id || resumeKey.value
    await showHistory(appId)
  } catch (error) {
    flash(error instanceof Error ? error.message : '续领失败', true)
  } finally {
    busy.value = false
  }
}

async function quickResume(appId: string) {
  resumeKey.value = appId
  await onResume()
}

async function onConfirm() {
  if (!resumeKey.value) return
  busy.value = true
  try {
    const result = await confirmClaim(resumeKey.value.trim())
    lastCredential.value = result.credential || result
    flash('领取回执已确认，该分片核清。')
    await refreshAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '确认回执失败', true)
  } finally {
    busy.value = false
  }
}

async function showHistory(appId: string) {
  try {
    const h = await loadHistory(appId)
    history.value = h.items || []
  } catch {
    history.value = []
  }
}

onMounted(async () => {
  newApplication()
  await refreshAll()
  // 若后端已在启动时播种题设场景，直接展示旧申请历史。
  if (!history.value.length) {
    const h = await loadHistory('APP-OLD-SHARD2')
    history.value = h.items || []
  }
})
</script>

<style scoped>
.dispatch-panel { margin: 14px 0 24px; border-top: 2px dashed var(--border); padding-top: 14px; }
.dp-head h3 { margin: 0 0 4px; }
.dp-tip { color: var(--muted); font-size: 13px; margin: 0 0 10px; max-width: 980px; line-height: 1.7; }
.dp-head-actions { display: flex; gap: 10px; }
.dp-message { margin: 10px 0 0; padding: 8px 12px; border-radius: 6px; background: #ecfdf5; color: #065f46; font-size: 13px; }
.dp-message.error { background: #fef2f2; color: #991b1b; }
.board-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 14px 0; }
.board-card { background: #fff; border: 1px solid var(--border); border-left-width: 4px; border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
.board-card.new { border-left-color: #2563eb; }
.board-card.pending { border-left-color: #d97706; }
.board-card.overlap { border-left-color: #7c3aed; }
.board-label { font-size: 13px; color: var(--muted); }
.pages { font-size: 22px; letter-spacing: 1px; }
.board-sub { font-size: 12px; color: var(--muted); }
.dp-grid { display: grid; grid-template-columns: minmax(320px, 5fr) minmax(420px, 7fr); gap: 14px; align-items: start; }
.dp-col { display: flex; flex-direction: column; gap: 14px; }
.dp-card { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.dp-card h4 { margin: 0 0 12px; font-size: 15px; }
.dp-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); margin-bottom: 10px; flex: 1; }
.dp-field input, .dp-field select { border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; font-size: 13px; color: #111; }
.dp-field.small { max-width: 110px; }
.dp-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; }
.dp-row .dp-field { margin-bottom: 10px; }
.dp-check { display: flex; gap: 8px; align-items: center; font-size: 13px; margin: 4px 0 12px; }
.dp-btn-row { display: flex; gap: 10px; align-items: center; }
.resume { background: #fffbeb; }
.replay-hint { font-size: 12.5px; margin: 10px 0 0; line-height: 1.8; }
.replay-tag { display: inline-block; margin-left: 8px; padding: 2px 8px; border-radius: 999px; background: #dcfce7; color: #166534; font-weight: 600; }
.plan-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
.plan-head { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
.plan-version { font-weight: 700; color: #1d4ed8; }
.plan-range { font-weight: 700; }
.plan-pages { color: var(--muted); font-size: 12px; }
.shard-chips { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; }
.shard-chip { font-size: 12px; border: 1px solid var(--border); border-radius: 999px; padding: 3px 10px; background: #f8fafc; }
.shard-chip.claimed { border-color: #93c5fd; background: #eff6ff; color: #1e40af; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12.5px; margin-top: 6px; }
.mini-table th, .mini-table td { border: 1px solid var(--border); padding: 5px 8px; text-align: left; }
.ok { color: #15803d; }
.warn { color: #b45309; font-weight: 600; }
.dp-empty { color: var(--muted); font-size: 13px; }
.cred-table { font-size: 12.5px; }
.cred-actions { white-space: nowrap; }
.cred-actions .link { margin-right: 8px; }
.history-list { list-style: none; margin: 12px 0 0; padding: 0; border-top: 1px dashed var(--border); }
.history-list li { display: flex; gap: 10px; padding: 7px 2px; border-bottom: 1px dashed var(--border); font-size: 13px; align-items: baseline; }
.history-seq { color: var(--muted); }
.history-event { flex: 1; }
.history-time { color: var(--muted); font-size: 12px; }
@media (max-width: 1100px) { .board-grid { grid-template-columns: 1fr; } .dp-grid { grid-template-columns: 1fr; } }
</style>
