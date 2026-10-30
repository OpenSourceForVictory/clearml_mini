import React, { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Config = { workspace_root: string; assets_root: string; cache_root: string; min_free_disk_gb: number };
type Storage = { revision: number; config: Config | null; all_applied: boolean; slots: {
  slot_id: string; gpu_index: number; status: string; applied_revision: number; reported_at: string | null;
  report: { effective?: Config; error?: string; capacity?: Record<string, number> };
}[] };
const labels: Record<string, string> = { applied: '已生效', pending: '待校验', waiting: '等待任务结束', failed: '校验失败', offline: '离线 / 报告过期', upgrade_required: '需升级 Worker', local: '本机配置（旧 Worker）' };
const empty: Config = {workspace_root: '', assets_root: '', cache_root: '', min_free_disk_gb: 2};

export function StorageSettings({id, name}: {id: string; name: string}) {
  const [data, setData] = useState<Storage | null>(null), [draft, setDraft] = useState<Config>(empty);
  const [error, setError] = useState(''), [message, setMessage] = useState(''), [busy, setBusy] = useState(false);
  const base = useRef<number | null>(null), dirty = useRef(false);
  const endpoint = `admin/nodes/${id}/storage`;
  function receive(value: Storage, reset = false) {
    setData(value);
    if (reset || !dirty.current) {
      setDraft(value.config || value.slots.find(s => s.report.effective)?.report.effective || empty);
      base.current = value.revision;
      dirty.current = false;
    }
  }
  useEffect(() => {
    let active = true;
    const refresh = () => api<Storage>(endpoint).then(v => { if (active) receive(v); }).catch(e => {if (active) setError(String(e));});
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {active = false; clearInterval(timer);};
  }, [endpoint]);
  async function save(restore: boolean) {
    if (base.current === null) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const value = await api<Storage>(endpoint, 'PUT', {expected_revision: base.current, config: restore ? null : draft});
      receive(value, true);
      setMessage(`版本 ${value.revision} 已保存，等待 Worker 校验确认。`);
    } catch (e) {setError(String(e));}
    finally {setBusy(false);}
  }
  return <details className="panel"><summary>{name} 存储配置</summary>
    <p>宿主机使用绝对路径；资产和缓存目录需预先存在。保存后等待各卡空闲、校验完成再生效。已有数据不会自动搬迁或删除。</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {data && <>
      <p>期望版本 {data.revision} · {data.config ? '平台配置' : '本机初始配置'} · {data.all_applied ? '全部已生效' : '尚未全部生效（请查看各卡状态）'}</p>
      {data.config && <p>期望目录：工作 {data.config.workspace_root}；资产 {data.config.assets_root}；缓存 {data.config.cache_root}；最低可用 {data.config.min_free_disk_gb} GiB</p>}
      <form onSubmit={e => {e.preventDefault(); void save(false);}}>
        {(['workspace_root', 'assets_root', 'cache_root'] as const).map((key, i) => <label key={key}>{['工作基目录', '资产目录', '缓存目录'][i]}
          <input aria-label={`${name} ${['工作基目录', '资产目录', '缓存目录'][i]}`} value={draft[key]} required pattern="/.*" onChange={e => {dirty.current = true; setDraft({...draft, [key]: e.target.value});}} />
        </label>)}
        <label>工作盘最低可用空间 GiB<input aria-label={`${name} 最低可用空间 GiB`} type="number" min={0} max={2097151} step="any" value={draft.min_free_disk_gb} onChange={e => {dirty.current = true; setDraft({...draft, min_free_disk_gb: Number(e.target.value)});}} required /></label>
        <button disabled={busy}>保存存储配置 / 重试校验</button>{' '}
        <button type="button" disabled={busy} onClick={() => void save(true)}>恢复本机配置</button>{' '}
        <button type="button" disabled={busy} onClick={() => receive(data, true)}>重新载入最新配置（放弃草稿）</button>
      </form>
      {!data.slots.length && <p>尚无已注册 GPU 槽位，等待 Worker 上报。</p>}
      {data.slots.map(slot => <article key={slot.slot_id}>
        <h4>GPU {slot.gpu_index} · {labels[slot.status] || slot.status} · 已确认版本 {slot.applied_revision}</h4>
        <p>最后报告：{slot.reported_at || '尚未上报'}</p>
        {slot.report.error && <p role="alert">{slot.report.error}</p>}
        {slot.report.effective && <>
          <p>实际工作目录：{slot.report.effective.workspace_root}</p><p>实际资产目录：{slot.report.effective.assets_root}</p><p>实际缓存目录：{slot.report.effective.cache_root}</p>
          <p>工作盘门槛：{slot.report.effective.min_free_disk_gb} GiB；可用：{Object.entries(slot.report.capacity || {}).map(([k,v]) => `${({workspace:'工作',assets:'资产',cache:'缓存'} as Record<string,string>)[k]} ${(v / 1024).toFixed(1)} GiB`).join(' / ')}</p>
          {(slot.report.capacity?.workspace ?? Infinity) < slot.report.effective.min_free_disk_gb * 1024 && <p role="alert">工作盘空间不足，新任务等待；空间恢复后自动继续。</p>}
        </>}
      </article>)}
    </>}
  </details>;
}
