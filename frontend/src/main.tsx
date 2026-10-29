import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

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
  created_at: string;
  attempts: Attempt[];
  error: string | null;
};
type Slot = {
  id: string;
  node_name: string;
  gpu_index: number;
  gpu_uuid: string;
  pool_id: string;
  online: boolean;
  quarantined: boolean;
  last_seen_at: string | null;
  gpu_model: string | null;
  vram_mb: number;
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
const errors: Record<string, string> = {
  "invalid credentials": "用户名或密码错误",
  "invalid token": "登录已过期，请重新登录",
  "pending limit": "排队任务已达上限，请等待部分任务完成",
  "previous execution cleanup is not confirmed":
    "正在等待 Worker 确认旧容器已停止，暂时无法重试",
  "node already provisioned": "该机器名称已注册",
  "username already exists": "用户名已存在",
  "pool name already exists": "资源池名称已存在",
};
async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  extra: Record<string, string> = {},
): Promise<T> {
  const token = sessionStorage.getItem("eval-token");
  const res = await fetch("/api/v1/" + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
      ...extra,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await res.json();
  if (!res.ok) {
    if (res.status === 401 && path !== "auth/login")
      window.dispatchEvent(new Event("eval-logout"));
    throw new Error(
      errors[value.detail] || value.detail || `请求失败 (${res.status})`,
    );
  }
  return value;
}
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
  const [user, setUser] = useState<User | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [tab, setTab] = useState("jobs");
  useEffect(() => {
    const logout = () => {
      sessionStorage.removeItem("eval-token");
      setUser(null);
    };
    window.addEventListener("eval-logout", logout);
    if (sessionStorage.getItem("eval-token"))
      api<User>("auth/me")
        .then(setUser)
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
    else setLoading(false);
    return () => window.removeEventListener("eval-logout", logout);
  }, []);
  async function login(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    const form = new FormData(e.currentTarget);
    try {
      const r = await api<{ access_token: string }>("auth/login", "POST", {
        username: form.get("username"),
        password: form.get("password"),
      });
      sessionStorage.setItem("eval-token", r.access_token);
      setUser(await api<User>("auth/me"));
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
  if (!user)
    return (
      <main className="login">
        <div className="login-card">
          <div className="brand-mark">M</div>
          <p className="eyebrow">MINI EVAL</p>
          <h1>评测工作台</h1>
          <p className="muted">提交评测，查看进度与结果。</p>
          <form onSubmit={login}>
            <Field label="用户名">
              <input name="username" autoComplete="username" required />
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
        </div>
      </main>
    );
  return (
    <div className="shell">
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
              onClick={() => setTab(id)}
            >
              {title}
            </button>
          ))}
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
                : "评测任务"}
          </span>
          <span className="live-dot">每 2 秒更新</span>
        </header>
        {tab === "jobs" || tab === "all" ? (
          <Jobs key={tab} all={tab === "all"} />
        ) : tab === "workers" ? (
          <Resources admin={user.role === "admin"} />
        ) : (
          <Admin />
        )}
      </main>
    </div>
  );
}
function Jobs({ all }: { all: boolean }) {
  const [benchmarks, setBenchmarks] = useState<{ id: string; name: string }[]>([]);
  const [benchmark, setBenchmark] = useState('mock');
  const [jobs, setJobs] = useState<Job[]>([]),
    [pools, setPools] = useState<Pool[]>([]),
    [selected, setSelected] = useState(""),
    [error, setError] = useState(""),
    [filter, setFilter] = useState(""),
    [page, setPage] = useState(1),
    [revision, setRevision] = useState(0),
    [submitting, setSubmitting] = useState(false);
  const submission = useRef({ body: "", key: "" });
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const [j, p, b] = await Promise.all([
          api<Job[]>(
            `${all ? "admin/jobs" : "jobs"}?page=${page}&page_size=20${filter ? "&status=" + filter : ""}`,
          ),
          api<Pool[]>("pools"),
          api<{ id: string; name: string }[]>("benchmarks"),
        ]);
        if (active) {
          setJobs(j);
          setPools(p);
          setBenchmarks(b);
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
  }, [all, page, filter, revision]);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    const form = new FormData(e.currentTarget);
    const body = {
      title: form.get("title"),
      model_ref: benchmark === 'robotwin-render' ? 'sapien-render' : form.get("model_ref"),
      episodes: Number(form.get("episodes")),
      pool_id: form.get("pool_id"),
      benchmark,
      gpu_mode: form.get("gpu_mode"),
      requested_vram_gb: Number(form.get("requested_vram_gb")),
      required_gpu_model: String(form.get("required_gpu_model") || "").trim() || null,
    };
    const serialized = JSON.stringify(body);
    if (submission.current.body !== serialized)
      submission.current = {
        body: serialized,
        key: Date.now() + "-" + Math.random().toString(36).slice(2),
      };
    try {
      const r = await api<{ id: string }>("jobs", "POST", body, {
        "Idempotency-Key": submission.current.key,
      });
      submission.current = { body: "", key: "" };
      setSelected(r.id);
      setRevision((x) => x + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">EVALUATIONS</p>
          <h1>{all ? "全部评测任务" : "我的评测任务"}</h1>
          <p className="muted">跟踪每一次评测，从排队到结果。</p>
        </div>
        <span className="pill">Mock 评测已开放</span>
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
      {!all && (
        <section className="panel">
          <h2>新建评测</h2>
          <form onSubmit={submit} className="submit-grid">
            <Field label="评测类型">
              <select value={benchmark} onChange={(e) => setBenchmark(e.target.value)}>
                {benchmarks.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </Field>
            <Field label="任务名称">
              <input
                name="title"
                placeholder="例如：基线模型验证"
                required
                maxLength={200}
              />
            </Field>
            {benchmark === 'mock' && <Field label="模型 / checkpoint 标识">
              <input
                name="model_ref"
                placeholder="model-v1"
                required
                maxLength={500}
              />
            </Field>}
            <Field label="资源池">
              <select key={benchmark} name="pool_id" required defaultValue="">
                <option value="" disabled>
                  选择资源池
                </option>
                {pools.filter((p) => benchmark === 'mock' || p.gpu_model !== 'virtual').map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {p.gpu_model === "virtual" ? "模拟池" : "异构 GPU 池"}{p.allow_shared ? " · 可共享" : " · GPU 独占"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="GPU 使用模式">
              <select name="gpu_mode" defaultValue="exclusive">
                <option value="exclusive">独占：每卡一个任务</option>
                <option value="shared">共享：需资源池开启并声明显存</option>
              </select>
            </Field>
            <Field label="所需显存 GiB（共享至少 1）">
              <input name="requested_vram_gb" type="number" min={0} max={10000} defaultValue={0} required />
            </Field>
            <Field label="指定 GPU 型号（可选，精确匹配）">
              <input name="required_gpu_model" maxLength={100} placeholder="留空使用池内任意合格 GPU" />
            </Field>
            <Field label="评测回合">
              <input
                name="episodes"
                type="number"
                min={1}
                max={benchmark === 'robotwin-render' ? 100 : 10000}
                defaultValue={10}
                required
              />
            </Field>
            <button className="primary" disabled={submitting || !pools.length}>
              {submitting ? "提交中…" : "提交评测"}
            </button>
          </form>
          <p className="hint">
            {benchmark === 'robotwin-render'
              ? '验证 GPU 上的 SAPIEN 物理仿真和图像渲染，不使用模型 checkpoint；通过率表示环境检查结果。'
              : '使用可重复的模拟评测。真实机器人任务与模型评测将在环境和资产验收后开放。'}
          </p>
        </section>
      )}
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
              <Badge status={job.status} />
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
                <th>活动任务 / 显存预留</th>
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
                  <td>{s.active_tasks} / {(s.reserved_vram_mb / 1024).toFixed(1)} GiB</td>
                  <td>{s.online ? "在线" : "离线"}</td>
                  <td>{s.quarantined ? "等待清理" : "正常"}</td>
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
function Admin() {
  const [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [nodes, setNodes] = useState<Node[]>([]),
    [runnerImage, setRunnerImage] = useState(""),
    [pools, setPools] = useState<Pool[]>([]),
    [credential, setCredential] = useState<{
      token: string;
      node_name: string;
    } | null>(null),
    [busy, setBusy] = useState(false);
  const refresh = () =>
    Promise.all([api<Node[]>("admin/nodes"), api<Pool[]>("pools"), api<{runner_image:string}>("admin/health")]).then(
      ([n, p, health]) => {
        setNodes(n);
        setPools(p);
        setRunnerImage(health.runner_image);
      },
    );
  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
  }, []);
  async function save(e: React.FormEvent<HTMLFormElement>, kind: string) {
    e.preventDefault();
    setError("");
    setMessage("");
    setBusy(true);
    const values: Record<string, unknown> = Object.fromEntries(
      new FormData(e.currentTarget).entries(),
    );
    for (const key of [
      "max_concurrent",
      "cpu_capacity",
      "ram_gb_capacity",
      "min_vram_gb",
      "cpu_per_job",
      "ram_gb_per_job",
      "max_tasks_per_gpu",
      "vram_reserve_gb",
      "max_gpu_utilization",
    ])
      if (key in values) values[key] = Number(values[key]);
    if (kind === "pools") values.allow_shared = values.allow_shared === "on";
    try {
      const result = await api<{ token: string; node_name: string }>(
        "admin/" + kind,
        "POST",
        values,
      );
      if (kind === "worker-credentials") setCredential(result);
      else setMessage("创建成功");
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  function download(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!credential) return;
    const f = new FormData(e.currentTarget);
    const value = {
      api_url: location.origin,
      token: credential.token,
      node_name: credential.node_name,
      pool_id: f.get("pool_id"),
      gpu_index: Number(f.get("gpu_index")),
      mock: f.get("mock") === "on",
      runner_image: runnerImage,
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `worker-${credential.node_name}-${value.gpu_index}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">ADMINISTRATION</p>
          <h1>管理设置</h1>
          <p className="muted">配置团队账号、资源池与评测机器。</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {message && <p className="success">{message}</p>}
      <div className="admin-grid">
        <section className="panel">
          <h2>创建成员</h2>
          <form onSubmit={(e) => save(e, "users")}>
            <Field label="用户名">
              <input name="username" required maxLength={100} />
            </Field>
            <Field label="初始密码（至少 12 位）">
              <input
                name="password"
                type="password"
                minLength={12}
                maxLength={200}
                required
                autoComplete="new-password"
              />
            </Field>
            <Field label="角色">
              <select name="role">
                <option value="user">成员</option>
                <option value="admin">管理员</option>
              </select>
            </Field>
            <button className="primary" disabled={busy}>
              创建账号
            </button>
          </form>
        </section>
        <section className="panel">
          <h2>创建资源池</h2>
          <form onSubmit={(e) => save(e, "pools")}>
            <Field label="名称">
              <input name="name" required />
            </Field>
            <Field label="设备类型">
              <select name="gpu_model" defaultValue="*">
                <option value="*">物理 GPU（允许不同型号）</option>
                <option value="virtual">模拟设备（无 GPU）</option>
              </select>
            </Field>
            <SharingFields />
            <div className="row">
              <Field label="最低显存 GiB">
                <input
                  name="min_vram_gb"
                  type="number"
                  min={0}
                  defaultValue={0}
                  required
                />
              </Field>
              <Field label="每任务 CPU 核">
                <input
                  name="cpu_per_job"
                  type="number"
                  min={1}
                  defaultValue={1}
                  required
                />
              </Field>
              <Field label="每任务内存 GiB">
                <input
                  name="ram_gb_per_job"
                  type="number"
                  min={1}
                  defaultValue={1}
                  required
                />
              </Field>
            </div>
            <button className="primary" disabled={busy}>
              创建资源池
            </button>
          </form>
        </section>
        <section className="panel">
          <h2>注册评测机器</h2>
          <form onSubmit={(e) => save(e, "worker-credentials")}>
            <Field label="机器名称">
              <input name="node_name" pattern="[a-zA-Z0-9_.-]+" required />
            </Field>
            <div className="row">
              <Field label="并发上限">
                <input
                  name="max_concurrent"
                  type="number"
                  min={1}
                  defaultValue={1}
                  required
                />
              </Field>
              <Field label="可分配 CPU 核">
                <input
                  name="cpu_capacity"
                  type="number"
                  min={1}
                  defaultValue={4}
                  required
                />
              </Field>
              <Field label="可分配内存 GiB">
                <input
                  name="ram_gb_capacity"
                  type="number"
                  min={1}
                  defaultValue={8}
                  required
                />
              </Field>
            </div>
            <button className="primary" disabled={busy}>
              生成 Worker 凭据
            </button>
          </form>
        </section>
        {credential && (
          <section className="panel credential">
            <h2>{credential.node_name} 的 Worker 配置</h2>
            <p className="hint">
              凭据仅在本次页面中提供。请下载配置并妥善保存；多卡机器按卡下载对应配置。
            </p>
            <form onSubmit={download}>
              <Field label="资源池">
                <select name="pool_id" required>
                  {pools.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="GPU 编号">
                <input
                  name="gpu_index"
                  type="number"
                  min={0}
                  defaultValue={0}
                />
              </Field>
              <label className="check">
                <input name="mock" type="checkbox" defaultChecked />无 GPU
                的模拟 Worker
              </label>
              <button className="primary">下载配置文件</button>
            </form>
          </section>
        )}
      </div>
      <section className="panel">
        <h2>资源池 GPU 策略</h2>
        <p className="hint">共享任务必须声明显存；同时受每卡、机器、用户及全局并发额度限制。降低额度不打断正在执行的任务。</p>
        {pools.filter(p => p.gpu_model !== "virtual").map(p => (
          <form key={p.id} onSubmit={async e => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            try {
              await api("admin/pools/" + p.id, "PATCH", {
                allow_shared: f.get("allow_shared") === "on",
                max_tasks_per_gpu: Number(f.get("max_tasks_per_gpu")),
                vram_reserve_gb: Number(f.get("vram_reserve_gb")),
                max_gpu_utilization: Number(f.get("max_gpu_utilization")),
              });
              setMessage("资源池策略已更新"); await refresh();
            } catch (e) { setError(String(e)); }
          }}>
            <h3>{p.name}</h3><SharingFields pool={p} /><button>保存 {p.name} 策略</button>
          </form>
        ))}
      </section>
      <section className="panel">
        <h2>已注册机器</h2>
        <table>
          <thead>
            <tr>
              <th>机器</th>
              <th>并发</th>
              <th>CPU / 内存</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((n) => (
              <tr key={n.id}>
                <td>{n.node_name}</td>
                <td>
                  <form className="row" onSubmit={async e => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    try {
                      await api("admin/nodes/" + n.id, "PATCH", {max_concurrent: Number(f.get("max_concurrent")), cpu_capacity: Number(f.get("cpu_capacity")), ram_gb_capacity: Number(f.get("ram_gb_capacity"))});
                      setMessage("机器并发额度已更新"); await refresh();
                    } catch (e) { setError(String(e)); }
                  }}>
                    <input aria-label={n.node_name + " 并发上限"} name="max_concurrent" type="number" min={1} max={1024} defaultValue={n.max_concurrent} required />
                    <input aria-label={n.node_name + " CPU 额度"} name="cpu_capacity" type="number" min={1} max={10000} defaultValue={n.cpu_capacity} required />
                    <input aria-label={n.node_name + " 内存额度 GiB"} name="ram_gb_capacity" type="number" min={1} max={100000} defaultValue={n.ram_gb_capacity} required />
                    <button>保存</button>
                  </form>
                </td>
                <td>
                  {n.cpu_capacity} 核 / {n.ram_gb_capacity} GiB
                </td>
                <td>{n.enabled ? "已启用" : "已禁用"}</td>
                <td>
                  <button
                    onClick={async () => {
                      try {
                        await api("admin/nodes/" + n.id, "PATCH", {
                          enabled: !n.enabled,
                        });
                        await refresh();
                      } catch (e) {
                        setError(String(e));
                      }
                    }}
                  >
                    {n.enabled ? "禁用" : "启用"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
