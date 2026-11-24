<template>
  <section class="tracker-panel" data-testid="credential-tracker-panel">
    <div class="tp-head">
      <h3>按调度批次凭据追踪 · 真实领取范围与分片结果</h3>
      <p class="tp-tip">批次追踪按调度批次凭据登记；刷新或重新进入后，这里展示的实际执行范围与已领取的计划版本一致。</p>
    </div>

    <form class="tp-lookup" data-testid="track-lookup-form" @submit.prevent="onLookup">
      <label class="tp-field grow">
        <span>调度批次凭据 / 申请单号 / 实际批次号</span>
        <input v-model="lookupKey" data-testid="track-key" placeholder="CRED-1 或 APP-OLD-SHARD2 或 B-OLD-SHARD2" />
      </label>
      <button class="btn primary" data-testid="track-submit" type="submit">查询真实领取范围</button>
      <button class="btn ghost" type="button" data-testid="track-refresh" @click="loadList">刷新凭据</button>
    </form>
    <p v-if="message" class="tp-message" :class="{ error: isError }" data-testid="track-message">{{ message }}</p>

    <article v-if="tracked" class="track-result" data-testid="track-result">
      <div class="tr-grid">
        <div><span>调度批次凭据</span><b data-testid="tr-credential">{{ tracked.credential_id }}</b></div>
        <div><span>实际批次</span><b data-testid="tr-batch">{{ tracked.batch_id }}</b></div>
        <div><span>计划版本</span><b data-testid="tr-version">{{ tracked.计划版本 }}</b></div>
        <div><span>分片序号</span><b data-testid="tr-shard">{{ tracked.shard_index }}</b></div>
        <div><span>分页范围</span><b data-testid="tr-range">{{ tracked.分页范围 }}</b></div>
        <div><span>真实领取页码</span><b data-testid="tr-pages">{{ formatPages(tracked.pages) }}</b></div>
        <div><span>分片结果</span><b data-testid="tr-shard-result">{{ tracked.分片结果 }}</b></div>
        <div><span>批次状态</span><b data-testid="tr-status">{{ tracked.批次状态 || tracked.batch_status || '执行中' }}</b></div>
      </div>
    </article>

    <table v-if="credentials.length" class="data-table tp-table" data-testid="tracker-credential-table">
      <thead>
        <tr>
          <th>调度批次凭据</th><th>计划版本</th><th>分页范围</th><th>真实领取页码</th>
          <th>实际批次</th><th>分片结果</th><th>回执状态</th><th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="c in credentials" :key="c.credential_id" :data-testid="`tracker-row-${c.credential_id}`">
          <td>{{ c.credential_id }}</td>
          <td data-testid="tracker-version">{{ c.计划版本 }}</td>
          <td data-testid="tracker-range">{{ c.分页范围 }}</td>
          <td data-testid="tracker-pages">{{ formatPages(c.pages) }}</td>
          <td data-testid="tracker-batch">{{ c.batch_id }}</td>
          <td data-testid="tracker-shard-result">{{ c.分片结果 || '待回传' }}</td>
          <td :class="c.needs_verification ? 'warn' : 'ok'">{{ c.needs_verification ? (c.receipt_status || '待核清') : c.status }}</td>
          <td><button class="link" type="button" :data-testid="`tracker-query-${c.credential_id}`"
                      @click="lookup(c.credential_id)">追踪</button></td>
        </tr>
      </tbody>
    </table>
    <p v-else class="tp-empty">暂无按调度批次凭据登记的批次，可先到「采集调度」领取分片。</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { loadBatchRange, loadCredentials, type AnyRecord } from '@/api/dispatch'

const lookupKey = ref('')
const tracked = ref<AnyRecord | null>(null)
const credentials = ref<AnyRecord[]>([])
const message = ref('')
const isError = ref(false)

function formatPages(pages?: number[]): string {
  if (!pages || !pages.length) return '—'
  return pages.join(', ')
}

async function lookup(key: string) {
  lookupKey.value = key
  await onLookup()
}

async function onLookup() {
  if (!lookupKey.value.trim()) return
  message.value = ''
  try {
    tracked.value = await loadBatchRange(lookupKey.value.trim())
    isError.value = false
    message.value = `已查到 ${tracked.value?.batch_id} 的真实领取范围。`
  } catch (error) {
    tracked.value = null
    isError.value = true
    message.value = error instanceof Error ? error.message : '查询失败'
  }
}

async function loadList() {
  try {
    const result = await loadCredentials()
    credentials.value = result.items || []
  } catch {
    credentials.value = []
  }
}

onMounted(loadList)
</script>

<style scoped>
.tracker-panel { margin: 14px 0 22px; border-top: 2px dashed var(--border); padding-top: 14px; }
.tp-head h3 { margin: 0 0 4px; }
.tp-tip { color: var(--muted); font-size: 13px; margin: 0 0 12px; }
.tp-lookup { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.tp-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.tp-field input { border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 13px; min-width: 320px; }
.tp-message { font-size: 13px; color: #065f46; }
.tp-message.error { color: #991b1b; }
.track-result { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 10px; padding: 14px 16px; margin: 12px 0; }
.tr-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.tr-grid div { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.tr-grid b { color: #0f172a; font-size: 14px; }
.tp-table { margin-top: 12px; font-size: 13px; }
.ok { color: #15803d; }
.warn { color: #b45309; font-weight: 600; }
.tp-empty { color: var(--muted); font-size: 13px; }
@media (max-width: 1000px) { .tr-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
