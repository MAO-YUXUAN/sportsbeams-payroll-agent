import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import { api, type Approval, type AuditEvent, type AuditResponse, type Job, type OutputFile, type RunContext } from './api'

const stateMeta: Record<string, { label: string; step: number; note: string }> = {
  created: { label: '等待核算', step: 1, note: '文件已就绪，可以开始检查和导入' },
  inputs_prepared: { label: '输入已检查', step: 2, note: '正在提取第三方结算数据' },
  waiting_additional_inputs: { label: '等待补充文件', step: 2, note: '可以按实际收到文件的时间逐个追加' },
  waiting_import_approval: { label: '等待导入审批', step: 2, note: '请确认第三方结算数据' },
  waiting_vendor_returns: { label: '等待第三方回传', step: 3, note: '15、16薪资表已生成，请发送给易才和亚润，并在确认后上传回传文件' },
  waiting_reconciliation_approval: { label: '等待核对审批', step: 3, note: '请确认工资表与结算表核对结果' },
  waiting_payment_approval: { label: '等待付款审批', step: 4, note: '请确认付款申请单' },
  completed: { label: '本月已完成', step: 5, note: '结果文件可以下载归档' },
  failed: { label: '处理失败', step: 1, note: '请查看异常信息后处理' },
}
const workflowStages = [
  { id: 1, icon: '夹', label: '收集输入资料' },
  { id: 2, icon: '表', label: '工资表滚月' },
  { id: 3, icon: '税', label: '累计个税公式更新' },
  { id: 4, icon: '勤', label: '考勤表确认' },
  { id: 5, icon: '餐', label: '餐补计算' },
  { id: 6, icon: '补', label: '补扣款计算' },
  { id: 7, icon: '三', label: '第三方数据核对（易才／亚润）' },
  { id: 8, icon: '沪', label: '上海员工社保／个税／公积金核对' },
  { id: 9, icon: '总', label: '汇总与总核对' },
]
const workflowPosition: Record<string, number> = {
  created: 1,
  inputs_prepared: 2,
  waiting_additional_inputs: 4,
  waiting_import_approval: 7,
  waiting_vendor_returns: 7,
  waiting_reconciliation_approval: 9,
  waiting_payment_approval: 9,
  completed: 10,
  failed: 1,
}
const stageName: Record<string, string> = { vendor_import: '结算数据导入', payroll_reconciliation: '工资核对', payment_request: '付款申请' }
const eventName: Record<string, string> = { run_created: '创建核算任务', inputs_prepared: '完成输入检查', payroll_month_rolled: '滚动生成本月工资表', workbook_structure_repaired: '修复工资表结构', run_file_received: '收到补充文件', meal_allowance_generated: '根据考勤生成餐补表', attendance_adjustments_calculated: '计算考勤补贴与缺勤扣款', attendance_adjustments_applied: '确认并写入考勤补扣款', new_employee_added_to_payroll: '新增员工到本月工资表', vendor_costs_extracted: '提取结算数据', vendor_payroll_outputs_generated: '生成易才和亚润对接薪资表', vendor_returns_reconciled: '核对第三方回传工资', payroll_updated: '更新并核对工资', payment_requests_generated: '生成付款申请', internal_payroll_values_updated: '将上海员工明细更新到本月工资表', internal_payment_request_generated: '生成上海员工付款申请单', internal_payment_request_blocked: '上海员工付款申请单核对未通过', vendor_payment_request_blocked: '第三方付款申请单核对未通过', run_completed: '完成本月核算' }
const categoryName: Record<string, string> = { yarun_settlement: '亚润结算表', yicai_dispatch_settlement: '易才派遣社保账单', payment_request_template: '社保公积金付款单模板', attendance_summary: '考勤月度汇总', yicai_payroll: '易才派遣薪资表（系统生成）', yarun_payroll: '亚润薪资表（系统生成）', payroll_bill: '易才回传工资账单', payroll_export: '亚润回传工资表', social_detail: '社保明细', tax_payment: '个税付款资料', housing_fund_detail: '公积金明细' }

const paymentTypeName: Record<string, string> = { social: '社保', tax: '个税', housing: '公积金' }
const fieldName: Record<string, string> = {
  pension_company: '单位养老', medical_company: '单位医疗', unemployment_company: '单位失业',
  injury_company: '单位工伤', supplemental_medical_company: '单位补充医疗', housing_company: '单位公积金',
  pension_employee: '个人养老', medical_employee: '个人医疗', unemployment_employee: '个人失业',
  housing_employee: '个人公积金',
}

function AuditDetails({ event }: { event: AuditEvent }) {
  if (!event.event_type.endsWith('_blocked')) return null
  const reconciliation = event.details.reconciliation as Record<string, unknown> | undefined
  if (!reconciliation) return null
  const differences = Array.isArray(reconciliation.differences) ? reconciliation.differences as Record<string, unknown>[] : []
  const missing = Array.isArray(reconciliation.missing_in_actual) ? reconciliation.missing_in_actual : []
  const extra = Array.isArray(reconciliation.extra_in_actual) ? reconciliation.extra_in_actual : []
  const paymentType = String(event.details.payment_type || event.details.vendor || '')
  return <details className="audit-details">
    <summary>查看核对差异（{differences.length + missing.length + extra.length} 项）</summary>
    <div className="audit-detail-body">
      {paymentType && <p><b>核对类型：</b>{paymentTypeName[paymentType] || paymentType}</p>}
      {missing.length > 0 && <p><b>工资表缺少：</b>{missing.map(String).join('、')}</p>}
      {extra.length > 0 && <p><b>工资表多出：</b>{extra.map(String).join('、')}</p>}
      {differences.length > 0 && <div className="audit-difference-table">
        <div className="audit-difference-head"><span>员工</span><span>项目</span><span>结算表</span><span>工资表</span><span>差额</span></div>
        {differences.map((item, index) => <div className="audit-difference-row" key={`${String(item.match_key)}-${String(item.field)}-${index}`}>
          <span>{String(item.employee_name || '-')}</span><span>{fieldName[String(item.field)] || String(item.field || '-')}</span>
          <span>{String(item.expected ?? '-')}</span><span>{String(item.actual ?? '-')}</span><span>{String(item.difference ?? '-')}</span>
        </div>)}
      </div>}
    </div>
  </details>
}

function WorkflowBoard({ run, outputs }: { run: RunContext; outputs: OutputFile[] }) {
  const [expanded, setExpanded] = useState<number | null>(null)
  const position = workflowPosition[run.state] ?? 1
  const failed = run.state === 'failed'
  const statusOf = (id: number) => position > id ? 'done' : position === id ? (failed ? 'failed' : 'active') : 'pending'
  const headline = failed ? '需处理异常' : run.state === 'completed' ? '已完成' : '进行中'
  const required = (run.summaries.required_files || []) as { category: string; received: boolean }[]
  const received = (category: string) => required.some(item => item.category === category && item.received)
  const generated = (...terms: string[]) => outputs.some(file => terms.every(term => `${file.name} ${file.relative_path}`.includes(term)))
  const defaultDone = (id: number) => statusOf(id) === 'done'
  const attendanceAdjustments = run.summaries.attendance_adjustments as { status?: string; details?: unknown[]; unmatched?: unknown[] } | undefined
  const adjustmentCalculated = Boolean(attendanceAdjustments?.details?.length)
  const adjustmentApplied = attendanceAdjustments?.status === 'completed'
  const attendanceReconciled = adjustmentCalculated && (attendanceAdjustments?.unmatched?.length ?? 0) === 0
  const attendanceConfirmed = adjustmentApplied
  const mealGeneration = run.summaries.meal_generation as { path?: string; record_count?: number } | undefined
  const mealCalculated = Boolean(mealGeneration?.record_count)
  const mealWorkbookGenerated = Boolean(mealGeneration?.path) || generated('餐补') || generated('餐费')
  const details: Record<number, { label: string; done: boolean; review?: string }[]> = {
    1: [
      { label: '上传基础工资表', done: Boolean((run.summaries.input_validation as { file_count?: number } | undefined)?.file_count) },
      { label: '识别文件类型与费用月份', done: defaultDone(1) },
      { label: '检查必需工作表与文件完整性', done: defaultDone(1) },
    ],
    2: [
      { label: '复制上月工资 Sheet', done: defaultDone(2) },
      { label: '更新本月 Sheet 名称和表头', done: defaultDone(2) },
      { label: '保持工作表顺序并另存', done: defaultDone(2) },
    ],
    3: [
      { label: '更新累计收入公式', done: defaultDone(3) },
      { label: '更新累计扣除和累计个税公式', done: defaultDone(3) },
      { label: '校验公式引用月份', done: defaultDone(3) },
    ],
    4: [
      { label: '接收并识别考勤月度汇总', done: received('attendance_summary') },
      { label: '核对出勤、缺勤和入离职信息', done: attendanceReconciled || defaultDone(4) },
      { label: '确认考勤数据可以用于核算', done: attendanceConfirmed || defaultDone(4), review: '人工确认' },
    ],
    5: [
      { label: '读取考勤餐补依据', done: received('attendance_summary') },
      { label: '按出勤口径计算餐补', done: mealCalculated },
      { label: '生成餐补工作簿', done: mealWorkbookGenerated },
    ],
    6: [
      { label: '汇总补发、补贴和其他加项', done: adjustmentCalculated || defaultDone(6) },
      { label: '汇总缺勤、代扣和其他减项', done: adjustmentCalculated || defaultDone(6) },
      { label: '写入工资表补扣款字段', done: adjustmentApplied || defaultDone(6), review: adjustmentCalculated && !adjustmentApplied ? '等待人工确认' : undefined },
    ],
    7: [
      { label: '亚润社保公积金', done: received('yarun_settlement') },
      { label: '易才社保公积金结算表', done: received('yicai_dispatch_settlement') },
      { label: '亚润工资表', done: generated('亚润', '薪资') },
      { label: '易才工资表', done: generated('易才', '薪资') },
      { label: '确认第三方结算数据可以导入', done: position > 7, review: '人工审批' },
      { label: '核对第三方回传工资差异', done: position > 8, review: '人工审批' },
    ],
    8: [
      { label: '社保明细', done: received('social_detail') },
      { label: '个税付款资料', done: received('tax_payment') },
      { label: '公积金明细', done: received('housing_fund_detail') },
      { label: '按员工核对社保、个税和公积金', done: defaultDone(8) },
    ],
    9: [
      { label: '汇总主工资表并执行总额核对', done: defaultDone(9) },
      { label: '确认工资核对结果', done: run.state === 'waiting_payment_approval' || run.state === 'completed', review: '人工审批' },
      { label: '生成付款申请', done: generated('付款') },
      { label: '确认付款申请并完成归档', done: run.state === 'completed', review: '人工审批' },
    ],
  }
  const effectiveStatus = (id: number) => {
    const baseStatus = statusOf(id)
    const items = details[id] || []
    if (baseStatus === 'failed') return 'failed'
    if (items.length > 0 && items.every(item => item.done)) return 'done'
    if (baseStatus === 'done') return 'done'
    if (items.some(item => item.done)) return 'active'
    return baseStatus
  }
  const doneCount = run.state === 'completed' ? 9 : workflowStages.filter(stage => effectiveStatus(stage.id) === 'done').length

  const node = (stage: typeof workflowStages[number], extra = '') => {
    const status = effectiveStatus(stage.id)
    return <div className={`workflow-node ${status} ${expanded === stage.id ? 'expanded' : ''} ${extra}`} key={stage.id} role="button" tabIndex={0} aria-expanded={expanded === stage.id} onClick={() => setExpanded(current => current === stage.id ? null : stage.id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setExpanded(current => current === stage.id ? null : stage.id) } }}>
      <b className="workflow-number">{stage.id}</b><span className="workflow-icon">{stage.icon}</span>
      <div><strong>{stage.label}</strong><small><i />{status === 'done' ? '已完成' : status === 'active' ? '进行中' : status === 'failed' ? '异常' : '待处理'}</small></div><span className="node-disclosure">{expanded === stage.id ? '−' : '+'}</span>
      {expanded === stage.id && <div className="node-details">{details[stage.id].map(item => <div key={item.label}><span>{item.label}{item.review && <em>{item.review}</em>}</span><b className={item.done ? 'ok' : 'missing'}>{item.done ? '✔' : '×'}</b></div>)}</div>}
    </div>
  }

  return <section className="workflow-board">
    <div className="workflow-summary"><span>当前进度：</span><strong>{doneCount}<em>/ 9</em></strong><b className={`workflow-headline ${failed ? 'failed' : ''}`}>{headline}</b><div className="workflow-track"><i style={{ width: `${doneCount / 9 * 100}%` }} /></div></div>
    <div className="workflow-map">
      {node(workflowStages[0], 'workflow-edge-node')}<span className="workflow-arrow" aria-hidden="true" />
      <div className="workflow-parallel"><b className="parallel-label">可并行处理</b><div className="workflow-logic">
        <div className="logic-row tax-flow">{node(workflowStages[1])}<span className="logic-arrow" aria-hidden="true" />{node(workflowStages[2])}</div>
        <div className="logic-row attendance-flow">{node(workflowStages[3])}<span className="logic-branch" aria-hidden="true"><i /><i /></span><div className="logic-stack">{node(workflowStages[4])}{node(workflowStages[5])}</div></div>
        {node(workflowStages[6], 'wide merge-source')}{node(workflowStages[7], 'wide merge-source')}
        <span className="merge-rail" aria-hidden="true" />
      </div></div>
      <span className="workflow-arrow" aria-hidden="true" />{node(workflowStages[8], 'workflow-edge-node')}
    </div>
    <div className="workflow-legend"><span className="done"><i />已完成</span><span className="active"><i />进行中</span><span className="pending"><i />待处理</span></div>
  </section>
}

function App() {
  const [online, setOnline] = useState(false), [runs, setRuns] = useState<RunContext[]>([])
  const [run, setRun] = useState<RunContext | null>(null), [approvals, setApprovals] = useState<Approval[]>([])
  const [outputs, setOutputs] = useState<OutputFile[]>([]), [audit, setAudit] = useState<AuditResponse | null>(null)
  const [tab, setTab] = useState<'overview' | 'audit'>('overview'), [busy, setBusy] = useState(''), [error, setError] = useState(''), [toast, setToast] = useState('')
  const [fileView, setFileView] = useState<'input' | 'output'>('input')
  const [jobProgress, setJobProgress] = useState<number | null>(null)
  const [showNew, setShowNew] = useState(false), [needsKey, setNeedsKey] = useState(false), [key, setKey] = useState('')
  const [actor, setActor] = useState(() => localStorage.getItem('payroll-actor') || '')
  const [chatOpen, setChatOpen] = useState(false), [chatText, setChatText] = useState(''), [messages, setMessages] = useState<{ role: string; text: string }[]>([])
  const shownAgentNotifications = useRef(new Set<string>())

  const refreshRun = useCallback(async (id: string) => {
    const [context, approvalData, outputData, auditData] = await Promise.all([api.getRun(id), api.approvals(id), api.outputs(id), api.audit(id)])
    setRun(context); setApprovals(approvalData.items); setOutputs(outputData.items); setAudit(auditData)
  }, [])
  const load = useCallback(async () => {
    try {
      await api.health(); setOnline(true); setError('')
      const [runData, llm] = await Promise.all([api.listRuns(), api.llmStatus()]); setRuns(runData.items); setNeedsKey(!llm.configured)
      if (run) await refreshRun(run.run_id)
    } catch (e) { setOnline(false); setError(e instanceof Error ? e.message : '后端服务未启动') }
  }, [refreshRun, run])
  useEffect(() => { void load() }, [])
  useEffect(() => { if (!toast) return; const id = setTimeout(() => setToast(''), 2800); return () => clearTimeout(id) }, [toast])
  useEffect(() => {
    if (!run) return
    const notifications = (run.summaries.agent_notifications || []) as { id?: string; status?: string; message?: string }[]
    const fresh = notifications.filter(item => item.status === 'unread' && item.id && item.message && !shownAgentNotifications.current.has(item.id))
    if (!fresh.length) return
    fresh.forEach(item => shownAgentNotifications.current.add(item.id!))
    setMessages(current => [...current, ...fresh.map(item => ({ role: 'agent', text: item.message! }))])
    setChatOpen(true)
  }, [run])

  async function poll(job: Job) { let current = job; setJobProgress(current.progress ?? 0); if (current.progress_message) setBusy(current.progress_message); while (['queued', 'running'].includes(current.status)) { await new Promise(r => setTimeout(r, 500)); current = await api.getJob(current.id); setJobProgress(current.progress ?? 0); if (current.progress_message) setBusy(current.progress_message) } if (current.status === 'failed') throw new Error(current.error || '任务执行失败'); setJobProgress(100); return current }
  async function advance() { if (!run) return; setBusy('正在运行核算流程…'); setJobProgress(0); setError(''); try { await poll(await api.advance(run.run_id)); await refreshRun(run.run_id); setToast('流程已推进到下一检查点') } catch (e) { setError(e instanceof Error ? e.message : '流程执行失败'); await refreshRun(run.run_id) } finally { setBusy(''); setJobProgress(null) } }
  async function decide(item: Approval, decision: 'approve' | 'reject') {
    if (!run) return; if (!actor.trim()) { setError('请先填写审批人姓名'); return }
    const comment = window.prompt(decision === 'approve' ? '审批备注（选填）' : '请输入拒绝原因', ''); if (comment === null) return
    if (decision === 'reject' && !comment.trim()) { setError('拒绝时必须填写原因'); return }
    setBusy('正在记录审批…'); try { localStorage.setItem('payroll-actor', actor.trim()); await api.decide(run.run_id, item.id, decision, actor, comment); await refreshRun(run.run_id); setToast(decision === 'approve' ? '审批已记录，可以继续流程' : '已拒绝并记录原因') } catch (e) { setError(e instanceof Error ? e.message : '审批失败') } finally { setBusy('') }
  }
  async function submitKey(e: React.FormEvent) { e.preventDefault(); setBusy('正在安全保存…'); try { await api.saveKey(key); setNeedsKey(false); setKey(''); setToast('DeepSeek API Key 已安全保存') } catch (e) { setError(e instanceof Error ? e.message : '保存失败') } finally { setBusy('') } }
  async function appendFiles(files: FileList | null) { if (!run || !files?.length) return; setError(''); try { for (let i = 0; i < files.length; i++) { setBusy(`正在追加 ${i + 1}/${files.length}：${files[i].name}`); await api.appendFile(run.run_id, files[i]) } await refreshRun(run.run_id); setToast('补充文件已接收') } catch (e) { setError(e instanceof Error ? e.message : '追加文件失败') } finally { setBusy('') } }
  async function deleteCurrentRun() { if (!run) return; const confirmed = window.confirm(`确定删除 ${run.expense_month} 的这条核算任务吗？\n\n任务将移动到本机回收目录，工资输出和审计记录不会立即永久清除。`); if (!confirmed) return; setBusy('正在将任务移入回收目录…'); setError(''); try { await api.deleteRun(run.run_id); const data = await api.listRuns(); setRuns(data.items); setRun(null); setApprovals([]); setOutputs([]); setAudit(null); setMessages([]); setToast('任务已移入回收目录') } catch (e) { setError(e instanceof Error ? e.message : '删除任务失败') } finally { setBusy('') } }
  async function sendChat(e: React.FormEvent) { e.preventDefault(); if (!chatText.trim()) return; const text = chatText.trim(); setChatText(''); setMessages(v => [...v, { role: 'user', text }]); setBusy('Agent 正在处理…'); setJobProgress(0); try { const done = await poll(run ? await api.chat(run.run_id, text) : await api.homeChat(text)); const result = done.result as { message?: string } | null; setMessages(v => [...v, { role: 'agent', text: result?.message || 'Agent 未返回回答，请重试或检查后端服务' }]); if (run) await refreshRun(run.run_id) } catch (e) { setMessages(v => [...v, { role: 'agent', text: e instanceof Error ? e.message : '暂时无法处理' }]) } finally { setBusy(''); setJobProgress(null) } }

  const meta = stateMeta[run?.state || 'created'] || stateMeta.created, pending = approvals.find(a => a.status === 'pending')
  const rawRequirements = (run?.summaries.required_files || []) as { category: string; received: boolean }[]
  const requirements = rawRequirements.filter(item => {
    if (run?.state === 'waiting_vendor_returns') return ['payroll_bill', 'payroll_export'].includes(item.category)
    return !['yicai_payroll', 'yarun_payroll', 'payroll_bill', 'payroll_export'].includes(item.category)
  })
  const cards = useMemo(() => { const source = run?.summaries || {}; const initialCount = (source.input_validation as { file_count?: number })?.file_count ?? 0; const appendedCount = Object.entries(run?.files || {}).filter(([category]) => category !== 'payroll_workbook').reduce((total, [, paths]) => total + paths.length, 0); return [
    { key: 'input' as const, label: '输入文件', value: String(initialCount + appendedCount), unit: '份' },
    { key: 'output' as const, label: '输出文件', value: String(outputs.length), unit: '份' },
  ] }, [run, outputs])

  return <div className="app-shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">S</span><div><strong>SPORTSBEAMS</strong><small>智能工资核算</small></div></div><div className="top-actions"><button className="primary top-new-run" onClick={() => setShowNew(true)}>＋ 新建月度核算</button><span className={`connection ${online ? 'online' : ''}`}><i />{online ? '本机服务正常' : '后端未连接'}</span><button className="icon-button" onClick={() => setNeedsKey(true)}>⚙</button></div></header>
    <main className={run ? 'detail-page' : 'home-page'}>
      {error && <div className="alert"><span>!</span><div><strong>需要处理</strong><p>{error}</p></div><button onClick={() => setError('')}>×</button></div>}
      <section className="workspace-grid"><aside className="run-list panel"><div className="panel-title"><div><span>核算记录</span><small>{runs.length} 个月度任务</small></div><button onClick={() => void load()}>↻</button></div><div className="run-items">{runs.length === 0 ? <div className="empty-small">还没有核算记录<br/><button onClick={() => setShowNew(true)}>创建第一个任务</button></div> : runs.map(item => { const m = stateMeta[item.state] || stateMeta.created; return <button className={`run-item ${run?.run_id === item.run_id ? 'active' : ''}`} key={item.run_id} onClick={() => { setMessages([]); setChatOpen(false); void refreshRun(item.run_id) }}><span className="month">{item.expense_month.replace('-', '年')}月</span><span className={`status-dot ${item.state}`} /><strong>{m.label}</strong><small>{new Date(item.updated_at).toLocaleDateString('zh-CN')}</small></button> })}</div></aside>
        <section className="main-panel panel">{!run ? <div className="empty-main"><div className="empty-icon">表</div><h2>从上月工资表开始</h2><p>先上传上月工资表，Agent 会滚动生成本月 Sheet；其他资料收到后再逐步追加。点击左侧任务可进入具体核算界面。</p><button className="primary" onClick={() => setShowNew(true)}>新建月度核算</button></div> : <><div className="run-heading"><div><button className="back-home" onClick={() => { setRun(null); setApprovals([]); setOutputs([]); setAudit(null); setMessages([]); setChatOpen(false) }}>← 返回首页</button><div className="run-kicker">{run.expense_month.replace('-', ' 年 ')} 月工资</div><h2>{meta.label}</h2><p>{meta.note}</p></div><div className="run-heading-actions"><button className="delete-run" onClick={() => void deleteCurrentRun()} disabled={!!busy} title="删除任务">删除</button><span className={`state-pill ${run.state}`}>{meta.label}</span></div></div>
          <WorkflowBoard run={run} outputs={outputs} />
          <div className="tabs"><button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}>核算概览</button><button className={tab === 'audit' ? 'active' : ''} onClick={() => setTab('audit')}>审计记录 <em>{audit?.count || 0}</em></button></div>
          {tab === 'overview' ? <div className="overview"><div className="summary-grid file-switcher">{cards.map(c => <button type="button" className={`metric ${fileView === c.key ? 'selected' : ''}`} key={c.label} onClick={() => setFileView(c.key)}><span>{c.label}</span><strong>{c.value}<small>{c.unit}</small></strong></button>)}</div>{run.last_error && <div className="error-card"><strong>最近一次异常</strong><p>{run.last_error}</p></div>}
            {fileView === 'input' && ['waiting_additional_inputs', 'waiting_vendor_returns'].includes(run.state) && <div className="input-stage"><div className="section-title"><div><span>{run.state === 'waiting_vendor_returns' ? '等待第三方确认并回传' : '按实际进度补充文件'}</span><small>{run.state === 'waiting_vendor_returns' ? '请先下载结果文件中的易才、亚润薪资表并分别发送；收到回传后再上传' : `已收到 ${requirements.filter(r => r.received).length}/${requirements.length} 类；未齐时流程会安全暂停`}</small></div></div><div className="requirement-grid">{requirements.map(item => <div className={`requirement ${item.received ? 'received' : ''}`} key={item.category}><i>{item.received ? '✓' : '○'}</i><span>{categoryName[item.category] || item.category}</span><b>{item.received ? '已收到' : '等待'}</b></div>)}</div><label className="append-button">＋ 追加收到的 Excel 文件<input type="file" multiple accept=".xlsx,.xls,.xlsm,.xlsb" onChange={e => { void appendFiles(e.target.files); e.target.value = '' }}/></label>{requirements.every(r => r.received) && <button className="primary stage-continue" onClick={() => void advance()}>文件已齐，继续核对 →</button>}</div>}
            {pending ? <div className="approval-card"><div className="approval-badge">待审批</div><div className="approval-copy"><span>{stageName[pending.stage] || pending.stage}</span><h3>{pending.summary}</h3><p>Agent 已暂停处理，等待人工确认后才会继续。</p></div><div className="approval-actions"><label>审批人<input value={actor} onChange={e => setActor(e.target.value)} placeholder="请输入姓名" /></label><div><button className="ghost danger" onClick={() => void decide(pending, 'reject')}>拒绝</button><button className="approve" onClick={() => void decide(pending, 'approve')}>确认通过</button></div></div></div> : run.state !== 'completed' && run.state !== 'failed' ? <div className="next-action"><div><span>下一步</span><h3>运行自动核算流程</h3><p>系统会处理到下一个人工审批节点后自动暂停。</p></div><button className="primary" onClick={() => void advance()} disabled={!!busy}>{busy || '开始处理 →'}</button></div> : null}
            {fileView === 'output' && <div className="files-card"><div className="section-title"><div><span>结果文件</span><small>均由本次核算运行生成</small></div><b>{outputs.length}</b></div>{outputs.length ? outputs.map(f => <div className="file-row" key={f.relative_path}><span className="file-icon">X</span><div><strong>{f.name}</strong><small>{(f.size / 1024).toFixed(1)} KB · {f.relative_path}</small></div><a href={api.downloadUrl(run.run_id, f.relative_path)}>下载</a></div>) : <div className="files-empty">当前还没有生成输出文件</div>}</div>}</div> : <div className="audit-view"><div className={`integrity ${audit?.verification.valid ? 'valid' : ''}`}><span>{audit?.verification.valid ? '✓' : '!'}</span><div><strong>{audit?.verification.valid ? '审计链完整' : '审计链需要检查'}</strong><small>{audit?.count || 0} 条记录已校验</small></div></div><div className="timeline">{audit?.items.slice().reverse().map(e => <div className={`event ${e.status}`} key={e.id}><i/><div><strong>{eventName[e.event_type] || e.event_type}</strong><p>{e.actor} · {e.status}</p><AuditDetails event={e}/></div><time>{new Date(e.timestamp).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</time></div>)}</div></div>}</>}</section></section></main>
    <button className="agent-fab" onClick={() => setChatOpen(v => !v)}><span>✦</span> 问 Agent</button>{chatOpen && <aside className="chat-panel"><div className="chat-head"><div><strong>工资核算 Agent</strong><small>{run ? '基于当前任务结果回答' : '首页助手 · 可回答使用和任务概览'}</small></div><button onClick={() => setChatOpen(false)}>×</button></div><div className="chat-body">{messages.length === 0 && <div className="agent-welcome"><span>✦</span><p>{run ? '可以问我当前进度、待审批事项或异常原因。' : '可以问我如何新建任务、准备哪些文件或查看任务概览。'}</p><button onClick={() => setChatText(run ? '解释当前核算状态' : '我应该如何开始工资核算？')}>{run ? '解释当前状态' : '如何开始？'}</button></div>}{messages.map((m, i) => <div key={i} className={`bubble ${m.role}`}>{m.text}</div>)}</div><form className="chat-form" onSubmit={sendChat}><input value={chatText} onChange={e => setChatText(e.target.value)} placeholder="输入问题…"/><button disabled={!chatText.trim()}>↑</button></form></aside>}
    {showNew && <NewRunModal onClose={() => setShowNew(false)} onCreated={async created => { setShowNew(false); setBusy('正在检查并滚动生成本月工资表…'); setJobProgress(0); try { await poll(await api.advance(created.run_id)); const data = await api.listRuns(); setRuns(data.items); await refreshRun(created.run_id); setToast('月度任务已创建，本月工资表已准备') } catch (e) { setError(e instanceof Error ? e.message : '工资表滚动处理失败'); await refreshRun(created.run_id) } finally { setBusy(''); setJobProgress(null) } }}/>} {needsKey && <div className="modal-backdrop"><form className="modal key-modal" onSubmit={submitKey}><button type="button" className="modal-close" onClick={() => setNeedsKey(false)}>×</button><div className="modal-symbol">钥</div><p className="eyebrow">FIRST-TIME SETUP</p><h2>需要配置 API Key</h2><p>当前电脑尚未配置 DeepSeek API Key。请输入管理员提供的 Key，它会由当前 Windows 用户加密保存在本机，不会写入工资文件或安装包。</p><div className="admin-contact"><span>没有 API Key？</span><a href="mailto:yuxuanmao336@gmail.com?subject=Sportsbeams%20Payroll%20Agent%20API%20Key">联系管理员：yuxuanmao336@gmail.com</a></div><label>DeepSeek API Key<input type="password" autoFocus value={key} onChange={e => setKey(e.target.value)} placeholder="sk-••••••••••••••••" /></label><button className="primary full" disabled={!!busy}>{busy || '安全保存并继续'}</button><small className="security-note">Windows DPAPI 加密 · 以后不再询问</small></form></div>}
    {busy && !needsKey && <div className="busy-bar"><div className="busy-copy"><span>{busy}</span>{jobProgress !== null && <b>{jobProgress}%</b>}</div>{jobProgress !== null ? <div className="progress-track"><i style={{ width: `${jobProgress}%` }}/></div> : <i className="spinner"/>}</div>}{toast && <div className="toast">✓ {toast}</div>}
  </div>
}

function NewRunModal({ onClose, onCreated }: { onClose: () => void; onCreated: (run: RunContext) => void }) {
  const previousMonth = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1), defaultMonth = `${previousMonth.getFullYear()}-${String(previousMonth.getMonth() + 1).padStart(2, '0')}`
  const [month, setMonth] = useState(defaultMonth), [files, setFiles] = useState<File[]>([]), [busy, setBusy] = useState(''), [error, setError] = useState(''); const input = useRef<HTMLInputElement>(null)
  async function submit(e: React.FormEvent) { e.preventDefault(); if (!files.length) { setError('请至少选择一个 Excel 文件'); return } try { setBusy('正在创建上传批次…'); const upload = await api.createUpload(month); for (let i = 0; i < files.length; i++) { setBusy(`正在上传 ${i + 1}/${files.length}：${files[i].name}`); await api.uploadFile(upload.upload_id, files[i]) } setBusy('正在创建核算任务…'); onCreated(await api.createRun(upload.upload_id)) } catch (e) { setError(e instanceof Error ? e.message : '创建失败') } finally { setBusy('') } }
  return <div className="modal-backdrop"><form className="modal new-run" onSubmit={submit}><button type="button" className="modal-close" onClick={onClose}>×</button><p className="eyebrow">NEW PAYROLL RUN</p><h2>新建月度核算</h2><p>选择费用月份，并上传包含上月 Sheet 的工资表。Agent 会先生成本月 Sheet，再等待后续资料。</p><label>费用月份<input type="month" value={month} onChange={e => setMonth(e.target.value)} required/></label><div className={`dropzone ${files.length ? 'has-files' : ''}`} onClick={() => input.current?.click()}><input ref={input} hidden type="file" accept=".xlsx,.xls,.xlsm,.xlsb" onChange={e => setFiles(Array.from(e.target.files || []))}/><span>{files.length ? '✓' : '↑'}</span><strong>{files.length ? `已选择 ${files.length} 个文件` : '选择上月工资表'}</strong><small>{files.length ? files.map(f => f.name).join('、') : '支持 xlsx、xls、xlsm、xlsb，单个不超过 50 MB'}</small></div>{error && <p className="form-error">{error}</p>}<div className="modal-actions"><button type="button" className="ghost" onClick={onClose}>取消</button><button className="primary" disabled={!!busy}>{busy || '创建核算任务'}</button></div></form></div>
}
export default App
