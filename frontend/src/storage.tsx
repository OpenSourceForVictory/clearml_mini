import React, { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Config = { workspace_root: string; assets_root: string; cache_root: string; artifact_root?: string | null; min_free_disk_gb: number };
type Storage = { revision: number; config: Config | null; all_applied: boolean; slots: {
  slot_id: string; gpu_index: number; accelerator_type?: string; config?: Config | null; status: string; applied_revision: number; reported_at: string | null;
  report: { effective?: Config; error?: string; capacity?: Record<string, number> };
}[] };
const labels: Record<string, string> = { applied: '已生效', pending: '待校验', waiting: '等待任务结束', failed: '校验失败', offline: '离线 / 报告过期', upgrade_required: '需升级 Worker', local: '本机配置（旧 Worker）' };
const empty: Config = {workspace_root: '', assets_root: '', cache_root: '', min_free_disk_gb: 2};
type Slot = Storage['slots'][number];
const deviceName=(slot:Slot)=>`${slot.accelerator_type==='npu'?'NPU':'GPU'} ${slot.gpu_index}`;
function initialSlotConfig(slot:Slot):Config {
  if(slot.config)return slot.config;
  const effective=slot.report.effective;
  if(!effective)return empty;
  const suffix='/slots/'+slot.slot_id;
  return {...effective,artifact_root:null,workspace_root:effective.workspace_root.endsWith(suffix)?effective.workspace_root.slice(0,-suffix.length):effective.workspace_root};
}

function SlotStorageEditor({slot,id,name,revision,onSaved,disabled}:{slot:Slot;id:string;name:string;revision:number;onSaved:(value:Storage)=>void;disabled:boolean}){
  const [draft,setDraft]=useState<Config>(()=>initialSlotConfig(slot));
  const [error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  const expected=useRef(revision),dirty=useRef(false);
  useEffect(()=>{if(!dirty.current){setDraft(initialSlotConfig(slot));expected.current=revision;}},[slot,revision]);
  const label=deviceName(slot);
  const suggested=(()=>{
    const current=draft.workspace_root.replace(/\/slots\/[0-9a-f-]+$/i,'');
    const device=(slot.accelerator_type==='npu'?'npu':'gpu')+slot.gpu_index;
    const machine=name.replace(/[^A-Za-z0-9_.-]/g,'_');
    let root=current.replace(new RegExp('/'+device+'$','i'),'');
    if(root.endsWith('/'+machine))root=root.slice(0,-machine.length-1);
    return root.replace(/\/$/,'')+'/'+machine+'/'+device;
  })();
  async function save(config:Config|null){
    setBusy(true);setError('');setMessage('');
    try{
      const value=await api<Storage>(`admin/nodes/${id}/storage/slots/${slot.slot_id}`,'PUT',{expected_revision:expected.current,config});
      dirty.current=false;expected.current=value.revision;onSaved(value);
      setMessage(config?'此设备目录已保存，等待 Worker 确认。':'已恢复整机默认或本机初始目录，等待 Worker 确认。');
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <details><summary>修改{label}目录</summary><p>只修改此设备；工作基目录实际生效为所填路径下的 slots/{slot.slot_id}，旧数据不会自动搬迁。</p>
    {error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    <form onSubmit={e=>{e.preventDefault();void save(draft);}}>
      {(['workspace_root','assets_root','cache_root'] as const).map((key,i)=><label key={key}>{['工作基目录','资产目录','缓存目录'][i]}<input aria-label={`${label} ${['工作基目录','资产目录','缓存目录'][i]}`} value={draft[key]} required pattern="/.*" onChange={e=>{dirty.current=true;setDraft({...draft,[key]:e.target.value});}}/></label>)}
      <label>产物根目录（选填）<input aria-label={`${label} 产物根目录`} value={draft.artifact_root||''} placeholder="留空继承整机产物目录" pattern="/.*" onChange={e=>{dirty.current=true;setDraft({...draft,artifact_root:e.target.value||null});}}/></label>
      <p>此卡产物目录留空时继承整机设置；整机也未设置时沿用旧工作区输出目录。工作、资产和缓存目录仍使用此卡配置。</p>
      <label>工作盘最低可用空间 GiB<input aria-label={`${label} 最低可用空间 GiB`} type="number" min={0} max={2097151} step="any" value={draft.min_free_disk_gb} onChange={e=>{dirty.current=true;setDraft({...draft,min_free_disk_gb:Number(e.target.value)});}} required/></label>
      <p>建议按机器和设备区分：{suggested}</p><button type="button" disabled={disabled||busy||!draft.workspace_root} onClick={()=>{dirty.current=true;setDraft({...draft,workspace_root:suggested});}}>采用建议工作目录</button>{' '}
      <button disabled={disabled||busy}>保存{label}目录</button>{' '}
      <button type="button" disabled={disabled||busy||!slot.config} onClick={()=>void save(null)}>恢复整机默认目录</button>{' '}
      <button type="button" disabled={busy} onClick={()=>{dirty.current=false;expected.current=revision;setDraft(initialSlotConfig(slot));setError('');}}>重新载入此设备配置</button>
    </form>
  </details>;
}

export function StorageSettings({id, name}: {id: string; name: string}) {
  const [data, setData] = useState<Storage | null>(null), [draft, setDraft] = useState<Config>(empty);
  const [error, setError] = useState(''), [message, setMessage] = useState(''), [busy, setBusy] = useState(false);
  const base = useRef<number | null>(null), dirty = useRef(false);
  const [machineDirty,setMachineDirty]=useState(false);
  const endpoint = `admin/nodes/${id}/storage`;
  function receive(value: Storage, reset = false) {
    setData(value);
    if (reset || !dirty.current) {
      setDraft(value.config || empty);
      base.current = value.revision;
      dirty.current = false;
      setMachineDirty(false);
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
  function savedSlot(value:Storage){receive(value,true);setMessage(`版本 ${value.revision} 已保存，等待各卡校验确认。`);}
  return <details className="panel"><summary>{name} 存储配置</summary>
    <p>整机默认目录作用于未单独设置的设备；每卡可在下方分别修改。宿主机使用绝对路径，资产和缓存目录需预先存在。已有数据不会自动搬迁或删除。</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {data && <>
      <p>期望版本 {data.revision} · {data.config ? '平台配置' : '本机初始配置'} · {data.all_applied ? '全部已生效' : '尚未全部生效（请查看各卡状态）'}</p>
      {data.config && <p>期望目录：工作 {data.config.workspace_root}；资产 {data.config.assets_root}；缓存 {data.config.cache_root}；最低可用 {data.config.min_free_disk_gb} GiB</p>}
      <form onSubmit={e => {e.preventDefault(); void save(false);}}><h4>整机默认目录</h4>
        {(['workspace_root', 'assets_root', 'cache_root'] as const).map((key, i) => <label key={key}>{['工作基目录', '资产目录', '缓存目录'][i]}
          <input aria-label={`${name} ${['工作基目录', '资产目录', '缓存目录'][i]}`} value={draft[key]} required pattern="/.*" onChange={e => {dirty.current = true;setMachineDirty(true); setDraft({...draft, [key]: e.target.value});}} />
        </label>)}
        <label>产物根目录（选填）<input aria-label={`${name} 产物根目录`} value={draft.artifact_root||''} placeholder="/共享目录/artifacts" pattern="/.*" onChange={e=>{dirty.current=true;setMachineDirty(true);setDraft({...draft,artifact_root:e.target.value||null});}}/></label>
        <p>配置后按批次/任务/执行生成公共产物与回合目录；不改变业务工作目录。需升级 Worker，保存后等待各卡确认。已有卡级配置启用产物继承后，请重新保存整机配置以同步；旧回执不一致时会等待确认。产物根必须与工作、资产、缓存目录分开，工作目录清理不删除这里的文件。</p>
        <label>工作盘最低可用空间 GiB<input aria-label={`${name} 最低可用空间 GiB`} type="number" min={0} max={2097151} step="any" value={draft.min_free_disk_gb} onChange={e => {dirty.current = true;setMachineDirty(true); setDraft({...draft, min_free_disk_gb: Number(e.target.value)});}} required /></label>
        <button disabled={busy}>保存存储配置 / 重试校验</button>{' '}
        <button type="button" disabled={busy} onClick={() => void save(true)}>恢复本机配置</button>{' '}
        <button type="button" disabled={busy} onClick={() => receive(data, true)}>重新载入最新配置（放弃草稿）</button>
      </form>
      {!data.slots.length && <p>尚无已注册 GPU 槽位，等待 Worker 上报。</p>}
      {data.slots.map(slot => <article key={slot.slot_id}>
        <h4>{deviceName(slot)} · {labels[slot.status] || slot.status} · 已确认版本 {slot.applied_revision}</h4>
        <p>最后报告：{slot.reported_at || '尚未上报'}</p>
        {slot.report.error && <p role="alert">{slot.report.error}</p>}
        {slot.report.effective && <>
          <p>实际产物根目录：{slot.report.effective.artifact_root || '沿用工作区输出目录'}</p><p>实际工作目录：{slot.report.effective.workspace_root}</p><p>实际资产目录：{slot.report.effective.assets_root}</p><p>实际缓存目录：{slot.report.effective.cache_root}</p>
          <p>工作盘门槛：{slot.report.effective.min_free_disk_gb} GiB；可用：{Object.entries(slot.report.capacity || {}).map(([k,v]) => `${({workspace:'工作',assets:'资产',cache:'缓存'} as Record<string,string>)[k]} ${(v / 1024).toFixed(1)} GiB`).join(' / ')}</p>
          {(slot.report.capacity?.workspace ?? Infinity) < slot.report.effective.min_free_disk_gb * 1024 && <p role="alert">工作盘空间不足，新任务等待；空间恢复后自动继续。</p>}
        </>}
        <SlotStorageEditor key={slot.slot_id} slot={slot} id={id} name={name} revision={data.revision} onSaved={savedSlot} disabled={machineDirty||busy}/>
      </article>)}
    </>}
  </details>;
}
