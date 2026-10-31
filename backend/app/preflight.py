"""Read-only inspection. No probes, reservations, reconciliation or persistence."""
from types import SimpleNamespace
from sqlalchemy import select, or_
from .core import Run, Job, Slot, Worker, now
from . import scheduling as admission, image_runtime

# Public explanations intentionally contain no host paths or other owners' jobs.
REASONS = {
    'offline': ('environment', '节点离线或已停用', '请管理员检查 Worker 在线状态'),
    'quarantined': ('environment', '节点正在等待旧执行清理', '等待 Worker 确认清理完成'),
    'membership_pending': ('environment', '资源池变更尚未生效', '等待旧任务清理及资源池变更生效'),
    'storage_pending': ('environment', '工作目录配置未生效或报告已过期', '请管理员检查存储配置及 Worker 心跳'),
    'runtime_pending': ('environment', '运行环境配置未生效或报告已过期', '请管理员检查镜像与资产配置及 Worker 心跳'),
    'disk_capacity': ('environment', '磁盘空间不足或磁盘报告已过期', '请管理员检查磁盘空间及心跳'),
    'client_image': ('environment', '所需镜像未批准或未就绪', '请管理员准备并批准客户端镜像'),
    'client_runtime': ('environment', '客户端运行环境未就绪', '请管理员检查 Worker 版本和逻辑资产'),
    'gpu_model': ('environment', 'GPU 型号不匹配', '选择其他资源池或调整指定 GPU 型号'),
    'shared_unsupported': ('environment', '节点不支持该共享请求', '检查 Worker 版本、共享策略和显存申请'),
    'vram_capacity': ('environment', 'GPU 显存总容量不足', '调整显存申请或选择更大显存的 GPU'),
    'telemetry_stale': ('environment', 'GPU 监测数据未就绪或已过期', '请管理员检查 GPU 监测及 Worker 心跳'),
    'gpu_busy': ('resource', 'GPU 已占用或达到共享任务上限', '等待现有任务释放资源'),
    'vram_busy': ('resource', '当前空闲显存不足', '等待显存释放或调整显存申请'),
    'gpu_utilization': ('resource', 'GPU 利用率达到准入门槛', '等待 GPU 利用率下降'),
    'node_concurrency': ('resource', '机器已达到并发上限', '等待机器上任务完成'),
    'node_cpu': ('resource', '机器 CPU 配额不足', '等待资源释放或请管理员检查机器配额'),
    'node_ram': ('resource', '机器内存配额不足', '等待资源释放或请管理员检查机器配额'),
    'global_concurrency': ('resource', '平台已达到并发上限', '等待正在执行的任务完成'),
    'user_concurrency': ('resource', '当前用户已达到并发上限', '等待自己的运行任务完成'),
    'endpoint_busy': ('resource', '推理服务正在被互斥任务使用', '等待使用该服务的任务完成'),
    'no_nodes': ('environment', '资源池中尚无 GPU 节点', '请管理员将适合的机器 GPU 加入资源池'),
}


def reason(code, message=None):
    category, text, action = REASONS[code]
    return dict(code=code, category=category, message=message or text, action=action)


def inspect_batch(s, body, user, pool, cases, configs):
    at = now()
    active = s.scalars(select(Run).where(or_(Run.status.in_(admission.ACTIVE), Run.cleanup_confirmed.is_(False)))).all()
    jobs = {r.job_id: s.get(Job, r.job_id) for r in active}
    slots = {x.id: x for x in s.scalars(select(Slot))}
    # Every case shares its template, endpoint and resource request.
    job = SimpleNamespace(client_spec=configs[0], required_gpu_model=body.required_gpu_model,
                          requested_vram_gb=body.requested_vram_gb, gpu_mode=body.gpu_mode)
    global_codes = []
    if len(active) >= admission.seconds('MAX_ACTIVE_GLOBAL', 2): global_codes.append('global_concurrency')
    if sum(jobs[r.job_id].owner_id == user.id for r in active) >= admission.seconds('MAX_ACTIVE_PER_USER', 2):
        global_codes.append('user_concurrency')
    if admission.endpoint_conflict(job, list(jobs.values())): global_codes.append('endpoint_busy')
    nodes = []
    for slot in sorted(slots.values(), key=lambda x: (x.node_name, x.gpu_index)):
        if slot.pool_id != pool.id: continue
        worker = s.get(Worker, slot.worker_id)
        held = [r for r in active if r.slot_id == slot.id]
        node_held = [r for r in active if r.slot_id in slots and slots[r.slot_id].worker_id == worker.id]
        codes = admission.slot_codes(worker, slot, at)
        codes += admission.eligibility_codes(slot, pool, job, held, at)
        codes += admission.node_codes(worker, pool, len(node_held), sum(r.reserved_cpu for r in node_held), sum(r.reserved_ram_gb for r in node_held))
        reasons = [reason(code) for code in dict.fromkeys(codes)]
        for message in image_runtime.blockers(worker, slot, job.client_spec, at):
            if message not in [r['message'] for r in reasons]:
                reasons.append(reason('client_runtime', message))
        nodes.append(dict(slot_id=slot.id, node_name=slot.node_name, gpu_index=slot.gpu_index,
                          gpu_model=slot.gpu_model, ready=not reasons and not global_codes, reasons=reasons))
    if not nodes: global_codes.append('no_nodes')
    reasons = [reason(code) for code in global_codes]
    ready = any(n['ready'] for n in nodes)
    # A resource wait is useful when at least one node needs no environment repair.
    resource_only = any(all(r['category'] == 'resource' for r in n['reasons']) for n in nodes)
    return dict(checked_at=at, can_submit=True, status='ready' if ready else 'waiting_resources' if resource_only else 'needs_environment',
                task_count=len(cases), total_episodes=sum(c.episodes for c in cases), max_concurrent=body.max_concurrent,
                cases=[c.model_dump() for c in cases], nodes=nodes, reasons=reasons,
                note='检查不预留资源；服务连通和客户端执行需要实际试跑验证。')
