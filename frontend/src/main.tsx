import {clearLogin, rememberLogin, watchSession} from "./login-session";
import {GuideProvider,GuideButton,PageHelp} from './user-guide';
import {EvaluationSetup} from './evaluation-setup';
import type {Suite} from './run-wizard';
import {Admin} from './administration';
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { api } from "./api";
import { Memberships, admissionLabels } from "./memberships";
import { ExecutionDataSettings, JobExecutionData } from "./retention";
import { StorageSettings } from "./storage";
import { RuntimeSettings, RuntimeReadiness } from "./image-runtime";
import { CSWorkbench, ArtifactList } from "./cs";

type User = { id: string; username: string; role: string };
type Pool = {
  id: string;
  name: string;
  gpu_model: string;
  min_vram_gb: number;
  allow_shared: boolean;
  max_tasks_per_gpu: number;
  vram_reserve_gb: number;
  max_gpu_utilization: number;
};
type Attempt = {
  endpoint?: string | null;
  endpoint_mode?: string | null;
  id: string;
  attempt: number;
  status: string;
  metrics: null | {
    success_rate: number;
    episode_count: number;
    runtime: number;
  };
  error: string | null;
  cleanup_confirmed: boolean;
  log_bytes: number;
};
type Job = {
  id: string;
  title: string;
  owner_id: string;
  owner_name: string;
  status: string;
  benchmark: string;
  model_ref: string;
  episodes: number;
  gpu_mode: string;
  requested_vram_gb: number;
  required_gpu_model: string | null;
  client_spec: null | {endpoint: string; task_name: string; seed: number; parameters: Record<string, unknown>};
  batch_id: string | null;
  case_id: string | null;
  created_at: string;
  attempts: Attempt[];
  error: string | null;
};
type Slot = {
  id: string;
  node_name: string;
  gpu_index: number;
  gpu_uuid: string;
  pool_id: string | null;
  admission_reasons?: string[];
  online: boolean;
  quarantined: boolean;
  last_seen_at: string | null;
  gpu_model: string | null;
  vram_mb: number;
  disk_free_mb: number | null;
  disk_min_free_mb: number | null;
  disk_fresh: boolean;
  free_vram_mb: number | null;
  gpu_utilization: number | null;
  telemetry_fresh: boolean;
  active_tasks: number;
  reserved_vram_mb: number;
};
type Node = {
  id: string;
  node_name: string;
  enabled: boolean;
  max_concurrent: number;
  cpu_capacity: number;
  ram_gb_capacity: number;
};
const labels: Record<string, string> = {
  PENDING: "排队中",
  RESERVED: "已分配",
  STARTING: "正在启动",
  RUNNING: "评测中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCEL_REQUESTED: "正在取消",
  CANCELLED: "已取消",
  TIMED_OUT: "已超时",
};
const terminal = ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"];
function Badge({ status }: { status: string }) {
  return (
    <span className={"badge " + status.toLowerCase()}>
      {labels[status] || status}
    </span>
  );
}
function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const id = React.useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {React.cloneElement(children as React.ReactElement<{ id: string }>, {
        id,
      })}
    </div>
  );
}
function App() {
  const [locked,setLocked]=useState(false), [sessionWarning,setSessionWarning]=useState(false);
  const [launch,setLaunch]=useState<{suite:Suite}|null>(null);
  const [setupJob,setSetupJob]=useState('');
  function runSuite(suite:Suite){setLaunch({suite});setTab('cs');location.hash='/cs';}
  const [user, setUser] = useState<User | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [tab, setTab] = useState(location.hash.startsWith('#/cs/batches/')||location.hash.startsWith('#/cs/compare/')||location.hash==='#/cs/results'?'results':location.hash.startsWith('#/cs')?'cs':'jobs');
  const [comparisonView,setComparisonView]=useState(location.hash.startsWith('#/cs/compare/'));
  useEffect(()=>{const change=()=>{setComparisonView(location.hash.startsWith('#/cs/compare/'));if(location.hash.startsWith('#/cs'))setTab(location.hash==='#/cs/results'||location.hash.startsWith('#/cs/batches/')||location.hash.startsWith('#/cs/compare/')?'results':'cs');};window.addEventListener('hashchange',change);return()=>window.removeEventListener('hashchange',change);},[]);
  useEffect(() => {
    const logout = () => {
      clearLogin();
      setLocked(false);setSessionWarning(false);setError("");
      setUser(null);
      setLaunch(null);setSetupJob('');setTab('jobs');
    };
    const expired=()=>{document.querySelectorAll<HTMLDialogElement>('dialog[open]').forEach(dialog=>dialog.close());setLocked(true);setError('登录已过期，请使用原账号重新登录。当前表单暂保留，请勿刷新页面。');};
    const warning=(e:Event)=>setSessionWarning(Boolean((e as CustomEvent).detail));
    window.addEventListener('eval-session-expired', expired);
    window.addEventListener('eval-session-warning', warning);
    const stopWatching=watchSession();
    window.addEventListener("eval-logout", logout);
    if (sessionStorage.getItem("eval-token"))
      api<User>("auth/me")
        .then(setUser)
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
    else setLoading(false);
    return () => {stopWatching();window.removeEventListener("eval-logout", logout);window.removeEventListener('eval-session-expired',expired);window.removeEventListener('eval-session-warning',warning);};
  }, []);
  useEffect(()=>{if(locked) document.querySelector<HTMLInputElement>('.login input[name="password"]')?.focus();},[locked]);
  async function login(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    const form = new FormData(e.currentTarget);
    try {
      const r = await api<{ access_token: string }>("auth/login", "POST", {
        username: form.get("username"),
        password: form.get("password"),
      });
      const next=await api<User>('auth/me','GET',undefined,{Authorization:'Bearer '+r.access_token});
      if(user && (next.id!==user.id || next.username!==user.username || next.role!==user.role)) throw new Error('请使用原账号登录；如需切换账号或角色已改变，请先退出并清除当前草稿。');
      rememberLogin(r.access_token);
      setUser(next);setLocked(false);setSessionWarning(false);
    } catch (e) {
      setError(String(e));
    }
  }
  if (loading)
    return (
      <main className="login">
        <p>正在加载工作台…</p>
      </main>
    );
  const loginView = (
      <main className="login">
        <div className="login-card">
          <div className="brand-mark">M</div>
          <p className="eyebrow">MINI EVAL</p>
          <h1>{user?'登录已过期':'评测工作台'}</h1>
          <p className="muted">提交评测，查看进度与结果。</p><p className="hint">使用管理员分配的账号登录；忘记密码请联系管理员。登录后可在“用户指南”查看操作与业务接入说明。</p>
          <form onSubmit={login}>
            <Field label="用户名">
              <input name="username" autoComplete="username" defaultValue={user?.username||''} required />
            </Field>
            <Field label="密码">
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </Field>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <button className="primary full">登录</button>
          </form>
          {user&&<button onClick={()=>window.dispatchEvent(new Event('eval-logout'))}>退出并清除当前草稿</button>}
        </div>
      </main>
    );
  if(!user) return loginView;
  return (
    <GuideProvider admin={user.role==='admin'}>
    {locked&&loginView}
    <div className="shell" hidden={locked} style={locked?{display:'none'}:undefined}>
      <aside>
        <div className="brand">
          <span className="brand-mark">M</span>
          <strong>Mini Eval</strong>
        </div>
        <div className="sidebar-label">评测工作区</div>
        <nav>
          {[
            ["jobs", "我的任务"],
            ["workers", "计算资源"],
            ["cs", "业务评测向导"],
            ["results", "评测运行与结果"],
            ...(user.role!=="admin"?[["setup", "评测配置"]]:[]),
            ...(user.role === "admin"
              ? [
                  ["all", "全部任务"],
                  ["admin", "管理设置"],
                ]
              : []),
          ].map(([id, title]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => {setTab(id);if(id==='cs'||id==='results')location.hash=id==='cs'?'/cs':'/cs/results';}}
            >
              {title}
            </button>
          ))}
          <GuideButton/>
        </nav>
        <div className="account">
          <strong>{user.username}</strong>
          <small>{user.role === "admin" ? "管理员" : "成员"}</small>
          <button
            onClick={() => window.dispatchEvent(new Event("eval-logout"))}
          >
            退出登录
          </button>
        </div>
      </aside>
      <main className="workspace">
        <header>
          <span>
            工作台 /{" "}
            {tab === "workers"
              ? "计算资源"
              : tab === "admin"
                ? "管理设置"
                : tab === "cs" ? "业务评测向导" : tab === "results" ? "评测运行与结果" : "评测任务"}
          </span>
          <span className="live-dot">{tab==='admin'?'管理工作台':tab==='results'&&comparisonView?'比较快照 · 手动刷新':'每 2 秒更新'}</span>
        </header>
        {sessionWarning&&!locked&&<p role="status">登录即将过期。继续操作可尝试续期；登录满 12 小时需重新登录。过期后当前表单暂保留，请勿刷新。</p>}
        <PageHelp topic={({jobs:"jobs",all:"jobs",workers:"resources",cs:"wizard",results:"results",setup:"start",admin:"start"} as Record<string,string>)[tab]||"start"}/>
        {tab === "jobs" || tab === "all" ? (
          <Jobs key={tab} all={tab === "all"} />
        ) : tab === "cs" || tab === "results" ? (
          <CSWorkbench initialLaunch={launch} pageView={tab==='results'?'records':'wizard'} admin={user.role === "admin"} renderJob={(id, close) => <JobDetail id={id} close={close} />} />
        ) : tab === "workers" ? (
          <Resources admin={user.role === "admin"} />
        ) : (
          tab==='setup'?<EvaluationSetup member onRun={runSuite} onJob={setSetupJob}/>:<Admin onRun={runSuite} onJob={setSetupJob}/>
        )}
        {setupJob&&<JobDetail id={setupJob} close={()=>setSetupJob('')}/>}
      </main>
    </div></GuideProvider>
  );
}
function Jobs({ all }: { all: boolean }) {
  const [jobs, setJobs] = useState<Job[]>([]),
    [selected, setSelected] = useState(""),
    [error, setError] = useState(""),
    [filter, setFilter] = useState(""),
    [page, setPage] = useState(1);
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const j = await api<Job[]>(`${all ? "admin/jobs" : "jobs"}?page=${page}&page_size=20${filter ? "&status=" + filter : ""}`);
        if (active) {
          setJobs(j);
          setError("");
        }
      } catch (e) {
        if (active) setError(String(e));
      }
    };
    void load();
    const timer = setInterval(load, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [all, page, filter]);
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">EVALUATIONS</p>
          <h1>{all ? "全部评测任务" : "我的评测任务"}</h1>
          <p className="muted">跟踪每一次评测，从排队到结果。</p>
        </div>

      </div>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      <div className="stats">
        <div>
          <small>本页任务</small>
          <strong>{jobs.length}</strong>
        </div>
        <div>
          <small>等待执行</small>
          <strong>
            {
              jobs.filter((j) =>
                ["PENDING", "RESERVED", "STARTING"].includes(j.status),
              ).length
            }
          </strong>
        </div>
        <div>
          <small>正在评测</small>
          <strong>{jobs.filter((j) => j.status === "RUNNING").length}</strong>
        </div>
        <div>
          <small>已完成</small>
          <strong>{jobs.filter((j) => j.status === "SUCCEEDED").length}</strong>
        </div>
      </div>
      <section className="panel">
        <div className="section-title">
          <h2>任务记录</h2>
          {
            <select
              aria-label="任务状态"
              value={filter}
              onChange={(e) => {
                setFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="">全部状态</option>
              {Object.entries(labels).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          }
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>任务</th>
                <th>状态</th>
                <th>提交人</th>
                <th>模型</th>
                <th>回合</th>
                <th>创建时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>
                    <strong>{j.title}</strong>
                    <small>{j.id.slice(0, 8)}</small>
                  </td>
                  <td>
                    <Badge status={j.status} />
                  </td>
                  <td>{j.owner_name}</td>
                  <td className="model">{j.model_ref}</td>
                  <td>{j.episodes}</td>
                  <td>{new Date(j.created_at).toLocaleString()}</td>
                  <td>
                    <button onClick={() => setSelected(j.id)}>查看详情</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!jobs.length && (
            <div className="empty">暂无任务。提交一个评测开始使用。</div>
          )}
        </div>
        {
          <div className="pagination">
            <button disabled={page === 1} onClick={() => setPage((x) => x - 1)}>
              上一页
            </button>
            <span>第 {page} 页</span>
            <button
              disabled={jobs.length < 20}
              onClick={() => setPage((x) => x + 1)}
            >
              下一页
            </button>
          </div>
        }
      </section>
      {selected && <JobDetail id={selected} close={() => setSelected("")} />}
    </>
  );
}
function JobDetail({ id, close }: { id: string; close: () => void }) {
  const [job, setJob] = useState<Job | null>(null),
    [attempt, setAttempt] = useState(""),
    [logs, setLogs] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const cursor = useRef(-1);
  useEffect(() => {
    let active = true;
    const load = () =>
      api<Job>("jobs/" + id)
        .then((j) => {
          if (active) setJob(j);
        })
        .catch((e) => active && setError(String(e)));
    void load();
    const t = setInterval(load, 2000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [id]);
  const run =
    job?.attempts.find((r) => r.id === attempt) || job?.attempts.at(-1);
  useEffect(() => {
    let active = true;
    cursor.current = -1;
    setLogs("");
    if (!run) return;
    const load = async () => {
      try {
        const r = await api<{
          chunks: { content: string }[];
          next_after: number;
        }>(
          `jobs/${id}/logs?run_id=${run.id}&after=${cursor.current}&limit=100`,
        );
        if (active) {
          cursor.current = r.next_after;
          setLogs((old) => old + r.chunks.map((c) => c.content).join(""));
        }
      } catch (e) {
        if (active) setError(String(e));
      }
    };
    void load();
    const t = setInterval(load, 2000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [id, run?.id]);
  async function action(kind: string) {
    setBusy(true);
    setError("");
    try {
      await api("jobs/" + id + "/" + kind, "POST");
      setJob(await api<Job>("jobs/" + id));
      if (kind === "retry") setAttempt("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="overlay">
      <section
        role="dialog"
        aria-modal="true"
        aria-label="任务详情"
        className="drawer"
      >
        <div className="section-title">
          <h2>{job?.title || "正在加载…"}</h2>
          <button onClick={close} aria-label="关闭详情">
            关闭 ×
          </button>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {job && (
          <>
            <div className="section-title">
              <Badge status={job.status} /><JobExecutionData key={id} id={id}/>
              <div className="actions">
                {!terminal.includes(job.status) && (
                  <button
                    disabled={busy || job.status === "CANCEL_REQUESTED"}
                    onClick={() => action("cancel")}
                  >
                    取消评测
                  </button>
                )}
                {["FAILED", "CANCELLED", "TIMED_OUT"].includes(job.status) && (
                  <button
                    className="primary"
                    disabled={busy || !job.attempts.at(-1)?.cleanup_confirmed}
                    onClick={() => action("retry")}
                  >
                    重新评测
                  </button>
                )}
              </div>
            </div>
            <dl>
              <dt>提交人</dt>
              <dd>{job.owner_name}</dd>
              <dt>{job.benchmark === 'robotwin-render' ? '环境检查' : '模型'}</dt>
              <dd>{job.model_ref}</dd>
              <dt>任务 ID</dt>
              <dd>{job.id}</dd>
              <dt>GPU 需求</dt>
              <dd>{job.gpu_mode === "shared" ? "共享" : "独占"} · {job.requested_vram_gb || "未指定"} GiB · {job.required_gpu_model || "不限型号"}</dd>
              <dt>回合数</dt>
              <dd>{job.episodes}</dd>
            </dl>
            {job.client_spec && <><h3>客户端配置</h3><pre className="logs">{JSON.stringify(job.client_spec, null, 2)}</pre></>}
            <Field label="执行尝试">
              <select
                value={run?.id || ""}
                onChange={(e) => setAttempt(e.target.value)}
              >
                {job.attempts.map((r) => (
                  <option key={r.id} value={r.id}>
                    第 {r.attempt} 次 · {labels[r.status]}
                  </option>
                ))}
              </select>
            </Field>
            {job.client_spec && <p>本次尝试推理服务：{run?.endpoint||'未分配'} {run?.endpoint_mode==='exclusive'?'独占':run?.endpoint_mode==='shared'?'共享':''}</p>}
            {run?.metrics && (
              <div className="stats metrics">
                <div>
                  <small>{job.benchmark === 'robotwin-render' ? '环境检查通过率' : '成功率'}</small>
                  <strong>
                    {(run.metrics.success_rate * 100).toFixed(1)}%
                  </strong>
                </div>
                <div>
                  <small>完成回合</small>
                  <strong>{run.metrics.episode_count}</strong>
                </div>
                <div>
                  <small>耗时</small>
                  <strong>{run.metrics.runtime.toFixed(1)}s</strong>
                </div>
              </div>
            )}
            {run?.error && <p className="error">{run.error}</p>}
            {run && !run.cleanup_confirmed && terminal.includes(run.status) && (
              <p className="hint">等待 Worker 确认旧容器已停止后，才可重试。</p>
            )}
            {job.benchmark === "cs-client" && <RuntimeReadiness id={id} />}
            {run && <ArtifactList jobId={id} runId={run.id} />}
            <h3>执行日志</h3>
            <p className="hint">每次尝试最多保存 256 KiB 日志。</p>
            <pre className="logs">
              {logs || "尚无日志，等待 Worker 开始执行。"}
            </pre>
          </>
        )}
      </section>
    </div>
  );
}
function Resources({ admin }: { admin: boolean }) {
  const [pools, setPools] = useState<Pool[]>([]),
    [slots, setSlots] = useState<Slot[]>([]),
    [summary, setSummary] = useState({ total: 0, online: 0 }),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const p = await api<Pool[]>("pools");
        const w = await api<Slot[] | { total: number; online: number }>(
          "workers",
        );
        if (active) {
          setPools(p);
          if (Array.isArray(w)) {
            setSlots(w);
            setSummary({
              total: w.length,
              online: w.filter((x) => x.online).length,
            });
          } else setSummary(w);
          setError("");
        }
      } catch (e) {
        if (active) setError(String(e));
      }
    };
    void load();
    const t = setInterval(load, 2000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, []);
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">RESOURCES</p>
          <h1>计算资源</h1>
          <p className="muted">按资源池选择适合的执行环境。</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="stats">
        <div>
          <small>注册槽位</small>
          <strong>{summary.total}</strong>
        </div>
        <div>
          <small>在线槽位</small>
          <strong>{summary.online}</strong>
        </div>
        <div>
          <small>资源池</small>
          <strong>{pools.length}</strong>
        </div>
      </div>
      <p className="hint">共享显存是调度预留，不是显存硬隔离；GPU 利用率用于辅助准入，不代表可保证的算力比例。</p>
      <div className="pool-grid">
        {pools.map((p) => (
          <section key={p.id} className="panel">
            <span className="eyebrow">RESOURCE POOL</span>
            <h2>{p.name}</h2>
            <p>{p.gpu_model === "virtual" ? "模拟池" : "异构 GPU 池"}</p>
            <p>{p.allow_shared ? `允许共享，每卡最多 ${p.max_tasks_per_gpu} 个任务` : "GPU 独占：每卡一个任务"}</p>
            <p className="muted">显存安全余量 {p.vram_reserve_gb} GiB · 共享准入利用率低于 {p.max_gpu_utilization}%</p>
            <p className="muted">最低显存 {p.min_vram_gb} GiB</p>
            <small>{p.id}</small>
          </section>
        ))}
      </div>
      {admin && (
        <section className="panel">
          <h2>Worker 槽位</h2>
          <table>
            <thead>
              <tr>
                <th>机器 / GPU</th>
                <th>型号 / 总显存</th>
                <th>空闲显存 / 利用率</th>
                <th>工作磁盘空闲 / 安全阈值</th><th>活动任务 / 显存预留</th>
                <th>连接</th>
                <th>准入</th>
                <th>最近心跳</th>
              </tr>
            </thead>
            <tbody>
              {slots.map((s) => (
                <tr key={s.id}>
                  <td>
                    {s.node_name} / {s.gpu_index}
                  </td>
                  <td>{s.gpu_model || "待登记"}<small>{(s.vram_mb / 1024).toFixed(1)} GiB</small></td>
                  <td>{s.telemetry_fresh ? `${((s.free_vram_mb || 0) / 1024).toFixed(1)} GiB / ${s.gpu_utilization}%` : "遥测过期或不可用"}</td>
                  <td>{s.disk_fresh ? `${((s.disk_free_mb||0)/1024).toFixed(1)} / ${((s.disk_min_free_mb||0)/1024).toFixed(1)} GiB${(s.disk_free_mb||0)<(s.disk_min_free_mb||0)?' · 空间不足，暂停准入':''}` : '未上报或已过期'}</td>
                  <td>{s.active_tasks} / {(s.reserved_vram_mb / 1024).toFixed(1)} GiB</td>
                  <td>{s.online ? "在线" : "离线"}</td>
                  <td>{s.admission_reasons?.map(r => admissionLabels[r] || r).join("、") || (s.quarantined ? "等待清理" : "基础就绪")}</td>
                  <td>
                    {s.last_seen_at
                      ? new Date(s.last_seen_at).toLocaleString()
                      : "尚未连接"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
function SharingFields({pool}: {pool?: Pool}) {
  return <>
    <label className="check"><input name="allow_shared" type="checkbox" defaultChecked={pool?.allow_shared || false} />允许 GPU 共享</label>
    <div className="row">
      <Field label="每卡任务上限"><input name="max_tasks_per_gpu" type="number" min={1} max={32} defaultValue={pool?.max_tasks_per_gpu || 1} required /></Field>
      <Field label="显存安全余量 GiB"><input name="vram_reserve_gb" type="number" min={0} max={10000} defaultValue={pool?.vram_reserve_gb ?? 2} required /></Field>
      <Field label="共享准入利用率阈值 %"><input name="max_gpu_utilization" type="number" min={1} max={100} defaultValue={pool?.max_gpu_utilization || 90} required /></Field>
    </div>
    <p className="hint">显存额度不提供硬隔离。独占是每卡一个任务，资源池内不同卡仍可并行。</p>
  </>;
}
createRoot(document.getElementById("root")!).render(<App />);
