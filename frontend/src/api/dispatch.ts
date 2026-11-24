/** 调度批次凭据 / 来源分片计划领取相关接口。
 *  后端同一组接口挂了多个前缀，这里统一用 /api/dispatch。
 */
import { request } from './client'

export type AnyRecord = Record<string, any>

async function parse<T = AnyRecord>(response: Response): Promise<T> {
  const text = await response.text()
  const data = text ? JSON.parse(text) : {}
  // 503 是「建批成功但响应中断」的模拟：响应体里仍带已落库的凭据。
  if (!response.ok && response.status !== 503) {
    throw new Error((data && (data.message || data.detail)) || `接口返回 ${response.status}`)
  }
  return data as T
}

export async function loadBoard(source: string): Promise<AnyRecord> {
  const response = await request(`/api/dispatch/board?source=${encodeURIComponent(source)}`)
  return parse(response)
}

export async function loadPlans(source: string): Promise<{ items: AnyRecord[] }> {
  const response = await request(`/api/dispatch/plans?source=${encodeURIComponent(source)}`)
  return parse(response)
}

export async function loadCredentials(source?: string): Promise<{ items: AnyRecord[] }> {
  const qs = source ? `?source=${encodeURIComponent(source)}` : ''
  const response = await request(`/api/dispatch/credentials${qs}`)
  return parse(response)
}

export async function confirmPlan(body: AnyRecord): Promise<AnyRecord> {
  const response = await request('/api/dispatch/plans', {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return parse(response)
}

export async function claimShard(body: AnyRecord): Promise<AnyRecord> {
  const response = await request('/api/dispatch/claims', {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return parse(response)
}

export async function resumeClaim(applicationId: string): Promise<AnyRecord> {
  const response = await request('/api/dispatch/resume', {
    method: 'POST',
    body: JSON.stringify({ application_id: applicationId }),
  })
  return parse(response)
}

export async function confirmClaim(applicationId: string): Promise<AnyRecord> {
  const response = await request('/api/dispatch/confirm', {
    method: 'POST',
    body: JSON.stringify({ application_id: applicationId }),
  })
  return parse(response)
}

export async function loadHistory(applicationId?: string, source?: string): Promise<{ items: AnyRecord[] }> {
  const params = new URLSearchParams()
  if (applicationId) params.set('application_id', applicationId)
  if (source) params.set('source', source)
  const response = await request(`/api/dispatch/history?${params.toString()}`)
  return parse(response)
}

export async function loadBatchRange(key: string): Promise<AnyRecord> {
  const response = await request(`/api/dispatch/batches/${encodeURIComponent(key)}/range`)
  return parse(response)
}

export async function loadScenario(source = 'SRC-PAGE'): Promise<AnyRecord> {
  const response = await request('/api/dispatch/scenario', {
    method: 'POST',
    body: JSON.stringify({ source, reset_first: true }),
  })
  return parse(response)
}

export async function resetDispatch(): Promise<AnyRecord> {
  const response = await request('/api/dispatch/reset', { method: 'POST' })
  return parse(response)
}
