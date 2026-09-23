// 後端 API 型別與呼叫（對應 backend/app/main.py）

export type GateStatus = 'pending' | 'ready' | 'approved'

export interface GateView {
  id: string
  step: number
  name: string
  rework_cost: string
  status: GateStatus
}

export interface BudgetSummary {
  used: number
  cap: number
  ratio: number
  level: 'ok' | 'warn' | 'alert' | 'hold'
  projected: number | null
  within_budget: boolean
}

export interface Estimate {
  production: number
  assets: number
  overhead: number
  total: number
}

export interface Project {
  id: string
  name: string
  target_s: number
  budget_cap: number
  settings: { resolution: string; fps: number; strategy: string; lip_sync: boolean; new_assets: number }
  step: number
  gate_list: GateView[]
  estimate: Estimate
  budget: BudgetSummary
  created_at: string
}

export interface Shot {
  shot_id: string
  description: string
  track: 'A' | 'B'
  lip_sync: boolean
  needs_split: boolean
  duration: { total_s: number; fps: number; frames: number; max_frames: number; locked: boolean; mode: string }
  dialogue: { text: string } | null
}

export interface WhatIf {
  label: string
  saving: number
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`)
  return body as T
}

const post = <T>(path: string, data?: unknown) =>
  call<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) })

export const api = {
  listProjects: () => call<Project[]>('/api/projects'),
  createProject: (data: { name: string; target_s: number; budget_cap: number }) =>
    post<Project>('/api/projects', data),
  getProject: (id: string) => call<Project>(`/api/projects/${id}`),
  listShots: (id: string) => call<Shot[]>(`/api/projects/${id}/shots`),
  parseStoryboard: (id: string, script: string) =>
    post<Shot[]>(`/api/projects/${id}/storyboard:parse`, { script }),
  gate: (id: string, gid: string, action: 'approve' | 'reject' | 'reopen') =>
    post<{ project: Project; invalidated: string[] }>(`/api/projects/${id}/gates/${gid}:${action}`),
  whatIf: (id: string) => call<WhatIf[]>(`/api/projects/${id}/budget:whatif`),
}

export function connectEvents(id: string, onEvent: (ev: Record<string, unknown>) => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/projects/${id}`)
  ws.onmessage = (m) => onEvent(JSON.parse(m.data))
  return ws
}
