"use client";

import { FormEvent, MouseEvent, useEffect, useMemo, useState } from "react";
import rawAreaData from "china-area-data/data.json";

type SystemKey = "ziwei" | "bazi" | "vedic";
type ProfessionalAudit = {
  auditId: string; stage: string; title: string; rawDataRefs: { documentId: string; pointer: string }[];
  theory: { system: string; ruleId: string; statement: string }[]; observations: string[]; inference: string[];
  counterEvidence: string[]; uncertainty: string[]; status: string;
};
type Analysis = { analysisType: string; professionalAudit?: ProfessionalAudit[]; claims: Claim[] };
type ComparisonStatus = { status: "locked" | "ready"; completedSystems: SystemKey[]; missingSystems?: SystemKey[]; reason?: string };
type SessionSummary = { sessionId: string; caseId: string; createdAt: string; expiresAt: string; completedSystems: SystemKey[]; comparisonCompleted: boolean };
type HistoryEntry = { sessionId: string; caseId: string; createdAt: string; summary?: SessionSummary };
type AnalysisProgress = {
  system: SystemKey | "integration"; state: "idle" | "queued" | "running" | "completed" | "failed"; currentStep?: number; totalSteps?: number;
  error?: { message?: string; code?: string; details?: unknown };
  label?: string; stageId?: string; stages?: { id: string; title: string }[];
  preview?: { title: string; note: string; items: { label: string; value: string }[] };
};
type Diagnostic = { key: string; status: string; events: unknown[]; repairs?: unknown[]; failure?: unknown };
type AreaMap = Record<string, Record<string, string>>;
type Claim = {
  claimId: string; claimKey?: string; claimScope?: { topic: string }; claim: string; rawDataRefs: { documentId: string; pointer: string }[];
  theory: { system: string; ruleId: string; statement: string }[]; inference: string[];
  counterEvidence: { searchSummary: string; items: { statement: string; impact: string }[] };
  consistency: { status: string; agreements: string[]; conflicts: string[] }; uncertainty: { birthTimeSensitivity?: string; items: string[] };
  gradeRationale?: { rawDataDirectness?: string; theoryChain?: string; counterEvidenceResistance?: string };
  validation: string[]; falsificationConditions: string[]; evidenceGrade: string; pollutionRisk: string;
  provisionalConclusion: { status?: string; text: string }; actionAdvice: { status: string; items: { text: string }[] };
};
type ClaimPresentation = "clear" | "conditional" | "mixed" | "time-sensitive" | "aligned" | "partial" | "conflict" | "insufficient" | "generation-incomplete";

const API = "/api";
const HISTORY_STORAGE_KEY = "astrology-research-history-v1";
const areaData = rawAreaData as AreaMap;
const areaOptions = (parentCode: string) => Object.entries(areaData[parentCode] ?? {}).map(([code, name]) => ({ code, name }));
const systems: { key: SystemKey; name: string; subtitle: string }[] = [
  { key: "ziwei", name: "紫微斗数", subtitle: "宫位、星曜与限运结构" },
  { key: "bazi", name: "子平八字", subtitle: "四柱、十神与节气时间轴" },
  { key: "vedic", name: "吠陀占星", subtitle: "恒星黄道、Dasha 与分盘审计" },
];
const hourOptions = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"));
const minuteOptions = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, "0"));
const progressTotals: Record<SystemKey | "integration", number> = { ziwei: 7, bazi: 9, vedic: 9, integration: 9 };
const claimGroups = [
  ["personality", "人格核心"], ["career", "职业工作"], ["wealth", "财富资源"], ["relationship", "关系互动"],
  ["learning", "学习成长"], ["family", "家庭居住"], ["social", "社交声誉"], ["health", "健康提醒"], ["timing", "阶段趋势"],
] as const;

function readHistory(): HistoryEntry[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(HISTORY_STORAGE_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is HistoryEntry => Boolean(item && typeof item === "object" && typeof (item as HistoryEntry).sessionId === "string" && typeof (item as HistoryEntry).caseId === "string" && typeof (item as HistoryEntry).createdAt === "string"));
  } catch { return []; }
}

function writeHistory(entries: HistoryEntry[]) {
  window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(entries.map(({ sessionId, caseId, createdAt }) => ({ sessionId, caseId, createdAt }))));
}

function formatHistoryDate(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "时间未知" : parsed.toLocaleString("zh-CN", { hour12: false });
}

function systemNames(keys: SystemKey[]) {
  return keys.map((key) => systems.find((system) => system.key === key)?.name ?? key).join("、");
}

class LocalApiError extends Error {
  constructor(message: string, readonly code?: string, readonly details?: unknown, readonly status?: number) { super(message); }
}

function displayError(error: unknown, fallback: string) {
  if (!(error instanceof LocalApiError)) return error instanceof Error ? error.message : fallback;
  if (typeof error.details === "string" && error.details) return `${error.message}（${error.details}）`;
  if (Array.isArray(error.details) && error.details.length) {
    const summary = error.details.slice(0, 2).map((item) => {
      if (typeof item !== "object" || !item) return String(item);
      const detail = item as { code?: unknown; path?: unknown; message?: unknown };
      return [detail.code, detail.path, detail.message].filter((part) => typeof part === "string" && part).join(" · ");
    }).join("；");
    return `${error.message}（${summary}）`;
  }
  return error.message;
}

async function request(path: string, options: RequestInit = {}) {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, { signal: AbortSignal.timeout(90000), ...options, headers: { "Content-Type": "application/json", ...(options.headers ?? {}) } });
  } catch { throw new LocalApiError("暂时无法连接服务，已保存的阶段不会丢失，请稍后继续。", "connection_lost"); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new LocalApiError(data.error?.message ?? "服务正在恢复，请稍后继续。", data.error?.code, data.error?.details, response.status);
  return data;
}

function List({ title, items }: { title: string; items: string[] }) {
  return <div className="evidence-row"><b>{title}</b>{items.length ? <ul>{items.map((item, i) => <li key={`${item}-${i}`}>{item}</li>)}</ul> : <span>未提供</span>}</div>;
}

function claimPresentation(claim: Claim, comparison = false): ClaimPresentation {
  const text = `${claim.claim} ${claim.provisionalConclusion.text}`;
  if (text.includes("结论生成待补全") || text.includes("模型未提供该主题的可验证研究草稿")) return "generation-incomplete";
  if (claim.provisionalConclusion.text.includes("无法判断") || claim.evidenceGrade === "D") return "insufficient";
  if (comparison) {
    if (claim.consistency.status === "aligned") return "aligned";
    if (claim.consistency.status === "partial") return "partial";
    if (claim.consistency.status === "conflict") return "conflict";
    return "insufficient";
  }
  if (claim.claimKey?.startsWith("timing.")) return "time-sensitive";
  if (claim.counterEvidence.items.length || /冲突|相反信号|牵制|并存/.test(text)) return "mixed";
  if (claim.gradeRationale?.rawDataDirectness === "strong" && claim.gradeRationale?.theoryChain === "complete") return "clear";
  return "conditional";
}

function claimStateCopy(state: ClaimPresentation) {
  return {
    clear: { label: "线索较明确", help: "多个相关专业阶段支持同一方向，暂未发现明显反向信号。" },
    conditional: { label: "有条件倾向", help: "本条有可追溯的单术依据，仍建议结合现实持续核验。" },
    mixed: { label: "存在相反信号", help: "同一体系内存在需要一并保留的正反信息。" },
    "time-sensitive": { label: "对时间敏感", help: "本条依赖当前大运、大限、Dasha 或具体流年窗口。" },
    aligned: { label: "三术一致", help: "三份冻结结论在主题、方向、时间范围和具体程度上相符。" },
    partial: { label: "部分一致", help: "三术在部分方向上相符，但具体程度或时间范围尚未完全对齐。" },
    conflict: { label: "明显冲突", help: "三术结论存在方向或具体程度差异，已保留原始冲突。" },
    insufficient: { label: "暂不下结论", help: "已有信息不足以支持稳定判断，不会强行补充推测。" },
    "generation-incomplete": { label: "生成待补全", help: "这是本条报告生成未完成，不是命盘结论；系统已自动尝试补全。" },
  }[state];
}

function openPicker(event: MouseEvent<HTMLInputElement>) {
  const input = event.currentTarget as HTMLInputElement & { showPicker?: () => void };
  input.showPicker?.();
}

function ClaimCard({ claim, comparison }: { claim: Claim; comparison: boolean }) {
  const state = claimPresentation(claim, comparison);
  const copy = claimStateCopy(state);
  const displayClaim = state === "insufficient" ? "本条暂不下结论" : claim.claim;
  return <article className="claim-card">
    <div className="claim-top"><p>{displayClaim}</p></div>
    <div className="claim-explanation"><b>本次说明</b><span>{state === "insufficient" ? claim.provisionalConclusion.text.replace(/^无法判断[：:]?\s*/, "") || copy.help : claim.provisionalConclusion.text}</span></div>
    <details className="reasoning"><summary>查看推演依据</summary><List title="推演依据" items={claim.inference} /></details>
    {comparison && <><List title="一致与部分一致" items={claim.consistency.agreements} /><List title="保留的冲突" items={claim.consistency.conflicts} /></>}
    {!comparison && state !== "insufficient" && claim.actionAdvice.status === "provided" && <List title="行动建议" items={claim.actionAdvice.items.map((item) => item.text)} />}
  </article>;
}

function ClaimSections({ claims, comparison }: { claims: Claim[]; comparison: boolean }) {
  const visibleClaims = claims.filter((claim) => claimPresentation(claim, comparison) !== "generation-incomplete");
  const groupedIds = new Set(visibleClaims.filter((claim) => claim.claimKey && claimGroups.some(([key]) => claim.claimKey?.startsWith(`${key}.`))).map((claim) => claim.claimId));
  const legacy = visibleClaims.filter((claim) => !groupedIds.has(claim.claimId));
  return <div className="claim-sections">{claimGroups.map(([key, title]) => {
    const rawGrouped = claims.filter((claim) => claim.claimKey?.startsWith(`${key}.`));
    const grouped = visibleClaims.filter((claim) => claim.claimKey?.startsWith(`${key}.`));
    if (!rawGrouped.length) return null;
    if (!grouped.length) return <section className="claim-group" key={key}><h4>{title}<span>待补全</span></h4><div className="module-incomplete">该模块尚未形成可展示内容。系统已完成自动补全尝试，未把技术性缺失作为命盘结论。</div></section>;
    const supported = grouped.filter((claim) => claimPresentation(claim, comparison) !== "insufficient");
    const insufficient = grouped.filter((claim) => claimPresentation(claim, comparison) === "insufficient");
    return <section className="claim-group" key={key}><h4>{title}<span>{supported.length ? `${supported.length} 条结论` : "暂无可展示结论"}</span></h4>{supported.map((claim) => <ClaimCard key={claim.claimId} claim={claim} comparison={comparison} />)}{insufficient.length ? <details className="insufficient-claims"><summary>{insufficient.length} 项暂不下结论</summary>{insufficient.map((claim) => <ClaimCard key={claim.claimId} claim={claim} comparison={comparison} />)}</details> : null}</section>;
  })}{legacy.length ? <section className="claim-group"><h4>既有结论<span>{legacy.length} 条</span></h4>{legacy.map((claim) => <ClaimCard key={claim.claimId} claim={claim} comparison={comparison} />)}</section> : null}</div>;
}

function ReportSummary({ claims, comparison = false }: { claims: Claim[]; comparison?: boolean }) {
  const states = claims.map((claim) => claimPresentation(claim, comparison));
  const clear = states.filter((state) => state === "clear").length;
  const conditional = states.filter((state) => state === "conditional").length;
  const mixed = states.filter((state) => state === "mixed").length;
  const timing = states.filter((state) => state === "time-sensitive").length;
  const aligned = states.filter((state) => state === "aligned").length;
  const partial = states.filter((state) => state === "partial").length;
  const conflicts = states.filter((state) => state === "conflict").length;
  const insufficient = states.filter((state) => state === "insufficient").length;
  const summary = comparison
    ? `三术一致 ${aligned} 条，部分一致 ${partial} 条，明显冲突 ${conflicts} 条`
    : `线索较明确 ${clear} 条，有条件倾向 ${conditional} 条，存在相反信号 ${mixed} 条，对时间敏感 ${timing} 条`;
  return <p className="report-summary">{summary}{insufficient ? `；另有 ${insufficient} 项暂不下结论，已折叠展示` : ""}。尚未完成的生成内容不会冒充命盘结论。</p>;
}

function AuditCard({ audit }: { audit: ProfessionalAudit }) {
  return <article className="audit-card">
    <div className="audit-top"><div><span className="audit-stage">专业推演阶段</span><h4>{audit.title}</h4></div></div>
    <List title="盘面观察" items={audit.observations} /><List title="阶段推演" items={audit.inference} />
    <List title="反向信号" items={audit.counterEvidence} /><List title="不确定因素" items={audit.uncertainty} />
  </article>;
}

function ProgressCard({ progress }: { progress: AnalysisProgress }) {
  const total = progress.totalSteps ?? progressTotals[progress.system];
  const current = Math.min(progress.currentStep ?? 0, total);
  const percent = progress.state === "completed" ? 100 : Math.min(94, Math.round((current / total) * 100));
  return <section className="progress-card" aria-live="polite">
    <div className="progress-head"><div><p className="eyebrow">ANALYSIS IN PROGRESS</p><h3>{progress.label ?? "正在准备分析。"}</h3></div><span>{progress.state === "completed" ? "已完成" : `${current} / ${total} · 处理中`}</span></div>
    <div className="progress-track" aria-label={`分析进度 ${current}/${total}`}><span style={{ width: `${percent}%` }} /></div>
    {progress.stages?.length ? <ol className="stage-timeline" aria-label="分析阶段">{progress.stages.map((stage) => {
      const activeIndex = progress.stages?.findIndex((item) => item.id === progress.stageId) ?? 0;
      const index = progress.stages?.findIndex((item) => item.id === stage.id) ?? 0;
      const state = index < activeIndex ? "completed" : index === activeIndex ? "active" : "pending";
      return <li className={state} key={stage.id}><span>{state === "completed" ? "✓" : index + 1}</span><b>{stage.title}</b></li>;
    })}</ol> : null}
    {progress.preview && <div className="chart-preview"><div><b>{progress.preview.title}</b><p>{progress.preview.note}</p></div><dl>{progress.preview.items.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></div>}
  </section>;
}

function DiagnosticCard({ diagnostic }: { diagnostic: Diagnostic }) {
  const failed = diagnostic.status === "failed";
  return <details className="diagnostic-card" open={failed}>
    <summary>{failed ? "查看失败报告" : "查看本次修复记录"}</summary>
    <p>仅在本匿名会话中展示；冻结报告和对应诊断最多保留 24 小时。</p>
    <pre>{JSON.stringify(diagnostic, null, 2)}</pre>
  </details>;
}

export default function Home() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [results, setResults] = useState<Partial<Record<SystemKey | "integration", Analysis>>>({});
  const [comparisonStatus, setComparisonStatus] = useState<ComparisonStatus | null>(null);
  const [busy, setBusy] = useState<SystemKey | "integration" | null>(null);
  const [progress, setProgress] = useState<AnalysisProgress | null>(null);
  const [diagnostics, setDiagnostics] = useState<Partial<Record<SystemKey, Diagnostic>>>({});
  const [message, setMessage] = useState("请先创建临时研究会话。");
  const [provinceCode, setProvinceCode] = useState("");
  const [cityCode, setCityCode] = useState("");
  const [locationPickerOpen, setLocationPickerOpen] = useState(false);
  const [birthTime, setBirthTime] = useState("");
  const [timePickerOpen, setTimePickerOpen] = useState(false);
  const [pendingHour, setPendingHour] = useState("15");
  const [pendingMinute, setPendingMinute] = useState("30");
  const [trueSolar, setTrueSolar] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyEntries, setHistoryEntries] = useState<HistoryEntry[]>([]);
  const [historyBusy, setHistoryBusy] = useState<string | null>(null);
  const provinces = areaOptions("86");
  const cities = areaOptions(provinceCode);
  const selectedPlace = useMemo(() => [areaData["86"]?.[provinceCode], areaData[provinceCode]?.[cityCode]].filter((name) => name && name !== "市辖区").join(""), [provinceCode, cityCode]);

  useEffect(() => { setHistoryEntries(readHistory()); }, []);

  function rememberSession(entry: HistoryEntry) {
    const next = [entry, ...readHistory().filter((item) => item.sessionId !== entry.sessionId)].slice(0, 20);
    writeHistory(next);
    setHistoryEntries(next);
  }

  async function refreshHistory() {
    const stored = readHistory();
    setHistoryEntries(stored);
    const resolved = await Promise.all(stored.map(async (entry) => {
      try { return { ...entry, summary: await request(`/sessions/${entry.sessionId}`) as SessionSummary }; }
      catch (error) { return error instanceof LocalApiError && error.status === 404 ? null : entry; }
    }));
    const available = resolved.filter((entry): entry is HistoryEntry => entry !== null);
    writeHistory(available);
    setHistoryEntries(available);
  }

  async function openHistory() {
    setHistoryOpen(true);
    await refreshHistory();
  }

  async function restoreHistory(entry: HistoryEntry) {
    setHistoryBusy(entry.sessionId);
    try {
      const summary = await request(`/sessions/${entry.sessionId}`) as SessionSummary;
      const completed = summary.completedSystems.filter((system): system is SystemKey => systems.some((item) => item.key === system));
      const loaded = await Promise.all(completed.map(async (system) => [system, (await request(`/sessions/${entry.sessionId}/analyses/${system}`) as { analysis: Analysis }).analysis] as const));
      const restored: Partial<Record<SystemKey | "integration", Analysis>> = Object.fromEntries(loaded);
      if (summary.comparisonCompleted) restored.integration = (await request(`/sessions/${entry.sessionId}/analyses/integration`) as { analysis: Analysis }).analysis;
      setSessionId(entry.sessionId);
      setResults(restored);
      setDiagnostics({});
      setProgress(null);
      setComparisonStatus({ status: completed.length === systems.length ? "ready" : "locked", completedSystems: completed, missingSystems: systems.filter((system) => !completed.includes(system.key)).map((system) => system.key) });
      rememberSession({ ...entry, caseId: summary.caseId, createdAt: summary.createdAt, summary });
      setHistoryOpen(false);
      setMessage(completed.length ? "已恢复历史会话与冻结报告。" : "已恢复历史会话，可继续完成单术分析。");
    } catch (error) {
      await refreshHistory();
      setMessage(displayError(error, "该历史会话已过期，无法恢复。"));
    } finally { setHistoryBusy(null); }
  }

  async function deleteHistory(entry: HistoryEntry) {
    if (!window.confirm("确定删除这条历史记录及其临时报告吗？此操作无法恢复。")) return;
    setHistoryBusy(entry.sessionId);
    try { await request(`/sessions/${entry.sessionId}`, { method: "DELETE" }); }
    catch (error) {
      if (!(error instanceof LocalApiError && error.status === 404)) {
        setMessage(displayError(error, "删除未完成，请重试。")); setHistoryBusy(null); return;
      }
    }
    const next = readHistory().filter((item) => item.sessionId !== entry.sessionId);
    writeHistory(next);
    setHistoryEntries(next);
    if (sessionId === entry.sessionId) {
      setSessionId(null); setResults({}); setDiagnostics({}); setComparisonStatus(null); setMessage("历史会话已删除。");
    }
    setHistoryBusy(null);
  }

  async function startSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy("integration"); setMessage("正在创建临时会话…");
    try {
      const data = await request("/sessions", { method: "POST", body: JSON.stringify({ date: form.get("date"), time: form.get("time"), place: form.get("place"), gender: form.get("gender"), baziTimeStandard: trueSolar ? "true_solar" : "civil" }) });
      setSessionId(data.sessionId); setResults({}); setDiagnostics({}); setComparisonStatus({ status: "locked", completedSystems: [], missingSystems: systems.map((system) => system.key) }); rememberSession({ sessionId: data.sessionId, caseId: data.caseId, createdAt: new Date().toISOString() }); setMessage("会话已创建。请分别完成三项单术分析。");
    } catch (error) { setMessage(displayError(error, "无法创建会话。")); } finally { setBusy(null); }
  }

  async function analyze(system: SystemKey) {
    if (!sessionId) return;
    setBusy(system);
    setDiagnostics((old) => { const next = { ...old }; delete next[system]; return next; });
    setProgress({ system, state: "running", currentStep: 0, totalSteps: progressTotals[system], label: "正在校验输入并生成确定性排盘。" });
    setMessage(`正在完成${systems.find((item) => item.key === system)?.name}的完整专业审计与研究分析。`);
    try {
      await request(`/sessions/${sessionId}/analyses/${system}`, { method: "POST" });
      const data = await waitForReport(sessionId, system);
      const diagnostic = await request(`/sessions/${sessionId}/analyses/${system}/diagnostic`) as Diagnostic;
      if (diagnostic.status !== "completed") setDiagnostics((old) => ({ ...old, [system]: diagnostic }));
      setResults((old) => ({ ...old, [system]: data.analysis }));
      setComparisonStatus(data.comparison as ComparisonStatus);
      setMessage(`${systemNames([system])}报告已生成，请下滑查看。`);
    } catch (error) {
      try {
        const diagnostic = await request(`/sessions/${sessionId}/analyses/${system}/diagnostic`) as Diagnostic;
        setDiagnostics((old) => ({ ...old, [system]: diagnostic }));
      } catch { /* The primary failure is shown above even if the diagnostic endpoint is unavailable. */ }
      setMessage(displayError(error, "分析未完成。"));
    }
    finally { setBusy(null); setProgress(null); }
  }

  async function waitForReport(id: string, system: SystemKey | "integration") {
    let interruptions = 0;
    for (;;) {
      let next: AnalysisProgress;
      try { next = await request(`/sessions/${id}/analyses/${system}/progress`) as AnalysisProgress; interruptions = 0; }
      catch (error) {
        const retryable = error instanceof LocalApiError && (error.code === "connection_lost" || (error.status ?? 0) >= 500);
        if (!retryable || ++interruptions > 6) throw error;
        setMessage("正在重新连接进度，后台已完成阶段会保留…");
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        continue;
      }
      if (next.state !== "idle") setProgress(next);
      if (next.state === "completed") return request(`/sessions/${id}/analyses/${system}`);
      if (next.state === "failed") throw new LocalApiError(next.error?.message ?? next.label ?? "分析中断，可以继续。", next.error?.code, next.error?.details);
      if (next.state === "idle") throw new LocalApiError("分析尚未开始，请点击开始分析。", "job_not_started");
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
    }
  }

  async function compare() {
    if (!sessionId) return; setBusy("integration"); setMessage("仅比较三份已冻结的单术结论，不生成统一人生结论…");
    try { await request(`/sessions/${sessionId}/comparison`, { method: "POST" }); const data = await waitForReport(sessionId, "integration"); setResults((old) => ({ ...old, integration: data.analysis })); setMessage("三术比较已完成，冲突已保留。"); }
    catch (error) { setMessage(displayError(error, "比较未完成。")); } finally { setBusy(null); setProgress(null); }
  }

  const allSinglesDone = systems.every((system) => Boolean(results[system.key]));
  const comparisonReady = comparisonStatus?.status === "ready" || allSinglesDone;
  const missingSystems = comparisonStatus?.missingSystems ?? systems.filter((system) => !results[system.key]).map((system) => system.key);
  const missingNames = missingSystems.map((key) => systems.find((system) => system.key === key)?.name).filter(Boolean).join("、");
  const anyReport = Object.values(results).some(Boolean);
  return <main className="shell">
    <header className="hero"><div className="hero-heading"><div><h1>三术命盘研究台</h1></div><button className="history-button" type="button" onClick={() => void openHistory()}>历史记录</button></div></header>
    {historyOpen && <section className="history-panel" aria-label="历史记录"><div className="history-heading"><div><p className="eyebrow">HISTORY</p><h2>历史记录</h2><p>仅显示此浏览器创建的匿名会话；报告保留 24 小时，到期后不可访问，过期数据将在服务清理时删除。</p></div><button type="button" onClick={() => setHistoryOpen(false)}>关闭</button></div>{historyEntries.length ? <div className="history-list">{historyEntries.map((entry) => <article key={entry.sessionId}><div><b>{formatHistoryDate(entry.summary?.createdAt ?? entry.createdAt)}</b><p>{entry.summary?.completedSystems.length ? `已完成：${systemNames(entry.summary.completedSystems)}${entry.summary.comparisonCompleted ? "；已完成三术比较" : ""}` : "会话已创建，尚未冻结单术报告"}</p></div><div><button type="button" disabled={historyBusy !== null} onClick={() => void restoreHistory(entry)}>{historyBusy === entry.sessionId ? "加载中…" : "查看"}</button><button className="history-delete" type="button" disabled={historyBusy !== null} onClick={() => void deleteHistory(entry)}>删除</button></div></article>)}</div> : <p className="history-empty">暂无仍在保留期内的历史记录。</p>}</section>}
    <section className="workspace">
      <form className="birth-form" onSubmit={startSession}><div className="section-heading"><span>01</span><div><h2>录入出生信息</h2><p>点击出生地点后依次选择省份、城市；时区和坐标由后端自动计算。</p></div></div><div className="form-grid">
        <label>出生日期<input name="date" type="date" required onClick={openPicker} /></label><div className="time-picker-field"><span>出生时间</span><button className={`time-field${birthTime ? "" : " placeholder"}`} type="button" onClick={() => { const [hour = "00", minute = "00"] = birthTime.split(":"); setPendingHour(hour); setPendingMinute(minute); setTimePickerOpen((open) => !open); }}>{birthTime || "请选择出生时间"}</button>{timePickerOpen && <div className="time-dropdown"><div className="time-columns"><div className="time-column" aria-label="小时">{hourOptions.map((hour) => <button type="button" className={pendingHour === hour ? "selected" : ""} key={hour} onClick={() => setPendingHour(hour)}>{hour}</button>)}</div><div className="time-column" aria-label="分钟">{minuteOptions.map((minute) => <button type="button" className={pendingMinute === minute ? "selected" : ""} key={minute} onClick={() => setPendingMinute(minute)}>{minute}</button>)}</div></div><button className="time-confirm" type="button" onClick={() => { setBirthTime(`${pendingHour}:${pendingMinute}`); setTimePickerOpen(false); }}>确认</button></div>}<input type="hidden" name="time" value={birthTime} /></div><div className="wide area-picker"><span>出生地点</span><button className="location-field" type="button" onClick={() => setLocationPickerOpen((open) => !open)}>{selectedPlace || "点击选择省份、城市"}</button>{locationPickerOpen && <div className="location-dropdown"><label>省份<select required value={provinceCode} onChange={(event) => { setProvinceCode(event.target.value); setCityCode(""); }}><option value="">选择省份</option>{provinces.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label><label>城市<select required disabled={!provinceCode} value={cityCode} onChange={(event) => { setCityCode(event.target.value); if (event.target.value) setLocationPickerOpen(false); }}><option value="">选择城市</option>{cities.map((item) => <option key={item.code} value={item.code}>{item.name === "市辖区" ? areaData["86"]?.[provinceCode] : item.name}</option>)}</select></label></div>}<input type="hidden" name="place" value={selectedPlace} /></div>
        <label>性别<select name="gender" defaultValue="女"><option value="女">女</option><option value="男">男</option></select></label>
      </div><label className="true-solar-option"><input type="checkbox" checked={trueSolar} onChange={(event) => setTrueSolar(event.target.checked)} />八字使用真太阳时校正（自动使用出生城市经度；仅影响子平八字）</label><button className="primary" type="submit" disabled={!birthTime || !cityCode || busy !== null}>{sessionId ? "重新创建临时会话" : "创建临时研究会话"}</button></form>
      <p className="status" aria-live="polite">{message}</p>
      {Object.values(diagnostics).some((diagnostic) => diagnostic && diagnostic.status !== "completed") ? <section className="diagnostics"><h2>运行诊断</h2>{systems.map((system) => diagnostics[system.key] ? <DiagnosticCard key={system.key} diagnostic={diagnostics[system.key]!} /> : null)}</section> : null}
      {progress && ["queued", "running", "completed"].includes(progress.state) && <ProgressCard progress={progress} />}
      <section className="analysis-panel"><div className="section-heading"><span>02</span><div><h2>分别进行三项分析</h2><p>每份单术报告独立保存，可在历史记录中查看。</p></div></div><div className="system-list">{systems.map((system) => <article className="system-card" key={system.key}><div><p className="system-label">{system.name}</p><p className="system-subtitle">{system.subtitle}</p></div><button type="button" disabled={!sessionId || busy !== null} onClick={() => analyze(system.key)}>{busy === system.key ? "分析中…" : results[system.key] ? "查看已生成报告" : "开始分析"}</button></article>)}</div></section>
      <section className={`comparison${comparisonReady ? " comparison-ready" : ""}`}><div className="section-heading"><span>03</span><div><h2>三术比较</h2><p>仅在三项单术都已冻结后解锁。</p></div></div><div className="comparison-body"><div><p>{comparisonReady ? "三项单术已冻结，可比较一致、部分一致、冲突与不确定性。" : sessionId ? `尚需完成：${missingNames || "三项单术分析"}。` : "请先创建临时研究会话，再完成三项单术分析。"}</p></div><button type="button" disabled={!comparisonReady || busy !== null} onClick={compare}>{busy === "integration" ? "比较中…" : results.integration ? "查看比较结果" : "开始三术比较"}</button></div></section>
      {anyReport && <section className="reports"><div className="report-heading"><div><p className="eyebrow">RESEARCH REPORT</p><h2>研究报告</h2></div><button className="export" type="button" onClick={() => window.print()}>导出 / 保存为 PDF</button></div>{systems.map((system) => results[system.key] && <section className="report-section" key={system.key}><h3>{system.name} · 单术分析</h3><ReportSummary claims={results[system.key]?.claims ?? []} />{results[system.key]?.professionalAudit?.length ? <details className="professional-audit"><summary>查看专业推演与盘面依据（{results[system.key]?.professionalAudit?.length} 个阶段）</summary>{results[system.key]?.professionalAudit?.map((audit) => <AuditCard key={audit.auditId} audit={audit} />)}</details> : null}<ClaimSections claims={results[system.key]?.claims ?? []} comparison={false} /></section>)}{results.integration && <section className="report-section integration-report"><h3>三术比较 · 仅展示一致与冲突</h3><ReportSummary claims={results.integration.claims} comparison /><ClaimSections claims={results.integration.claims} comparison /></section>}</section>}
    </section><footer><span>研究模式：验证解释力，而非证明命盘必然正确。</span><span>导出按钮使用浏览器“保存为 PDF”，不上传报告。</span><a href="/api/source" target="_blank" rel="noreferrer">开源代码与许可</a></footer>
  </main>;
}
