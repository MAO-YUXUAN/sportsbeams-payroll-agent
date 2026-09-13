const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8765'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    try { message = (await response.json()).detail || message } catch { /* non-JSON response */ }
    throw new ApiError(message, response.status)
  }
  return response.json() as Promise<T>
}

export const api = {
  base: API_BASE,
  health: () => request<{ status: string }>('/health'),
  llmStatus: () => request<{ configured: boolean; provider: string }>('/api/v1/settings/llm'),
  saveKey: (apiKey: string) => request('/api/v1/settings/llm', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: apiKey }) }),
  listRuns: () => request<{ items: RunContext[]; count: number }>('/api/v1/runs'),
  getRun: (id: string) => request<RunContext>(`/api/v1/runs/${id}`),
  deleteRun: (id: string) => request<{ deleted: boolean; recoverable: boolean }>(`/api/v1/runs/${id}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm_run_id: id }) }),
  createUpload: (month: string) => request<{ upload_id: string }>('/api/v1/uploads', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expense_month: month }) }),
  uploadFile: (uploadId: string, file: File) => { const form = new FormData(); form.append('file', file); return request(`/api/v1/uploads/${uploadId}/files`, { method: 'POST', body: form }) },
  createRun: (uploadId: string) => request<RunContext>('/api/v1/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ upload_id: uploadId }) }),
  advance: (id: string) => request<Job>(`/api/v1/runs/${id}/advance`, { method: 'POST' }),
  appendFile: (runId: string, file: File) => { const form = new FormData(); form.append('file', file); return request(`/api/v1/runs/${runId}/files`, { method: 'POST', body: form }) },
  getJob: (id: string) => request<Job>(`/api/v1/jobs/${id}`),
  approvals: (id: string) => request<{ items: Approval[] }>(`/api/v1/runs/${id}/approvals`),
  decide: (runId: string, approvalId: string, decision: 'approve' | 'reject', actor: string, comment: string) => request<Approval>(`/api/v1/runs/${runId}/approvals/${approvalId}/decision`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, decided_by: actor, comment }) }),
  outputs: (id: string) => request<{ items: OutputFile[] }>(`/api/v1/runs/${id}/outputs`),
  audit: (id: string) => request<AuditResponse>(`/api/v1/runs/${id}/audit`),
  chat: (id: string, message: string) => request<Job>(`/api/v1/runs/${id}/chat`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message, actor: 'user' }) }),
  homeChat: (message: string) => request<Job>('/api/v1/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message, actor: 'user' }) }),
  downloadUrl: (runId: string, path: string) => `${API_BASE}/api/v1/runs/${runId}/outputs/${path.split('/').map(encodeURIComponent).join('/')}`,
}

export interface RunContext { run_id: string; expense_month: string; state: string; created_at: string; updated_at: string; files: Record<string, string[]>; summaries: Record<string, unknown>; outputs: Record<string, string>; last_error: string | null }
export interface Job { id: string; run_id: string; status: string; result: Record<string, unknown> | null; error: string | null; progress: number; progress_message: string }
export interface Approval { id: string; stage: string; status: string; summary: string; requested_at: string; decided_by?: string; comment?: string; details: Record<string, unknown> }
export interface OutputFile { name: string; relative_path: string; size: number }
export interface AuditEvent { id: string; timestamp: string; event_type: string; status: string; actor: string; details: Record<string, unknown> }
export interface AuditResponse { items: AuditEvent[]; count: number; verification: { valid: boolean; event_count: number; errors: string[] } }
