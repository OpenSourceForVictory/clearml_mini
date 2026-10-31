import React, {useEffect, useState} from 'react';
import {api} from './api';

export const admissionLabels:Record<string,string> = {
  offline:'离线', disabled:'已禁用', unassigned:'未分配', draining:'排空中', blocked:'目标不可用',
  pool_unavailable:'资源池不可用', cleanup_pending:'等待清理', storage_pending:'存储未确认',
  runtime_pending:'运行环境未确认', disk_unknown:'磁盘报告过期', disk_full:'磁盘空间不足',
};
type Card = {id:string;node_name:string;gpu_index:number;gpu_model:string;vram_mb:number;
  pool_id:string|null;desired_pool_id:string|null;membership_revision:number;membership_applied_revision:number;
  membership_state:string;membership_error:string|null;admission_reasons:string[];online:boolean;active_tasks:number};
type Pool = {id:string;name:string;member_count:number;online_count:number;ready_count:number;incoming_count:number;
  pending_jobs:number;active_jobs:number;models:Record<string,{count:number;vram_mb:number}>};
export function Memberships() {
  const [cards,setCards]=useState<Card[]>([]), [pools,setPools]=useState<Pool[]>([]);
  const [filter,setFilter]=useState('all'), [target,setTarget]=useState('');
  const [selected,setSelected]=useState<Record<string,number>>({});
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[submitted,setSubmitted]=useState<Record<string,number>>({});
  async function refresh() {
    const [c,p]=await Promise.all([api<Card[]>('workers'),api<Pool[]>('pools')]);
    setCards(c);setPools(p);
  }
  useEffect(()=>{void refresh().catch(e=>setError(String(e)));const timer=setInterval(()=>void refresh().catch(e=>setError(String(e))),5000);return()=>clearInterval(timer);},[]);
  const name=(id:string|null)=>pools.find(p=>p.id===id)?.name || (id ? id : '未分配');
  const visible=cards.filter(c=>filter==='all'||(filter==='unassigned'?c.pool_id===null:c.pool_id===filter));
  const groups=[...new Set(visible.map(c=>c.node_name))];
  const chosen=cards.filter(c=>c.id in selected);
  const conflict=chosen.some(c=>c.membership_revision!==selected[c.id]);
  const submittedCards=cards.filter(c=>c.id in submitted);
  const completed=submittedCards.filter(c=>c.membership_applied_revision>=submitted[c.id]).length;
  const detail=pools.find(p=>p.id===filter);
  function toggle(group:Card[],checked:boolean) {
    setSelected(old=>{const next={...old};for(const c of group)if(checked)next[c.id]=c.membership_revision;else delete next[c.id];return next;});
  }
  async function save() {
    setBusy(true);setError('');
    try {
      const result=await api<Array<{slot_id:string;membership_revision:number}>>('admin/resource-memberships','PUT',{
        pool_id:target||null,members:Object.entries(selected).map(([slot_id,expected_revision])=>({slot_id,expected_revision}))});
      setSubmitted(Object.fromEntries(result.map(c=>[c.slot_id,c.membership_revision])));
      setSelected({});await refresh();
    } catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <section className="panel">
    <h2>资源池成员</h2>
    <p className="hint">先注册机器，再选择已连接的 GPU 加入池。同机各卡可加入不同池；CPU、内存和并发额度按整机合计。</p>
    <label>查看成员来源 <select aria-label="成员来源" value={filter} onChange={e=>setFilter(e.target.value)}>
      <option value="all">全部设备</option><option value="unassigned">未分配设备</option>
      {pools.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    {detail && <div className="hint">
      <p>{detail.name}：现有 {detail.member_count} 卡，在线 {detail.online_count}，基础就绪 {detail.ready_count}，待加入 {detail.incoming_count}；排队 {detail.pending_jobs}，执行中 {detail.active_jobs}。</p>
      {Object.entries(detail.models||{}).map(([model,v])=><p key={model}>{model}：{v.count} 卡 / {(v.vram_mb/1024).toFixed(1)} GiB</p>)}
    </div>}
    <p className="hint">基础就绪仍需匹配具体任务的镜像、显存、型号及额度。迁移中的卡只计入实际所在池。</p>
    {!visible.length && <p>没有符合条件的设备。新机器启动 Worker 并连接后会出现在这里。</p>}
    {groups.map(group=>{const members=visible.filter(c=>c.node_name===group);return <fieldset key={group}>
      <legend><label><input type="checkbox" aria-label={group+' 全选当前显示 GPU'} checked={members.every(c=>c.id in selected)}
        onChange={e=>toggle(members,e.target.checked)}/>{group} · 当前显示 {members.length} 卡</label></legend>
      <table><thead><tr><th>选择</th><th>设备</th><th>当前 → 期望资源池</th><th>状态</th><th>基础准入</th></tr></thead>
      <tbody>{members.map(c=><tr key={c.id}>
        <td><input type="checkbox" aria-label={group+' GPU '+c.gpu_index} checked={c.id in selected} onChange={e=>toggle([c],e.target.checked)}/></td>
        <td>GPU {c.gpu_index} · {c.gpu_model} · {(c.vram_mb/1024).toFixed(1)} GiB</td>
        <td>{name(c.pool_id)} → {name(c.membership_revision ? c.desired_pool_id : c.pool_id)}</td>
        <td>{c.online?'在线':'离线'} · {admissionLabels[c.membership_state]||'已分配'} · {c.active_tasks} 个持有执行{c.membership_error&&<p role="alert">{c.membership_error}</p>}</td>
        <td>{(c.admission_reasons||[]).map(r=>admissionLabels[r]||r).join('、')||'基础就绪'}</td>
      </tr>)}</tbody></table>
    </fieldset>;})}
    <label>目标资源池 <select aria-label="目标资源池" value={target} onChange={e=>setTarget(e.target.value)}>
      <option value="">移出资源池（未分配）</option>{pools.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}
    </select></label>
    <p>已选择 {chosen.length} 卡 → {name(target||null)}。{chosen.filter(c=>c.active_tasks>0).length} 卡持有执行，需等待任务及清理结束。</p>
    {chosen.map(c=><p key={c.id} className="hint">{c.node_name} GPU {c.gpu_index}：{name(c.pool_id)} → {name(target||null)}</p>)}
    {conflict&&<p role="alert">成员版本已变化，请刷新选择后再提交。</p>}
    <div className="row"><button className="primary" disabled={busy||!chosen.length||conflict} onClick={()=>void save()}>确认变更选中 GPU</button>
      <button disabled={busy} onClick={()=>setSelected(Object.fromEntries(chosen.map(c=>[c.id,c.membership_revision])))}>刷新选择版本</button>
      <button onClick={()=>setSelected({})}>清空选择</button></div>
    {error&&<p role="alert" className="error">{error}</p>}
    {!!submittedCards.length&&<p role="status">本批已生效 {completed}/{submittedCards.length}；其余设备等待排空或修正目标。{submittedCards.some(c=>c.membership_revision>submitted[c.id])?'部分设备已有更新的管理操作，请查看当前状态。':''}</p>}
  </section>;
}
