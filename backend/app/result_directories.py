"""Freeze a template's approved, read-only browse directory at first claim."""
import re
from pathlib import PurePosixPath as Path
from fastapi import HTTPException
from sqlalchemy import select
from .core import ExecutionBrowseRoot
from . import shared_storage

TOKEN=re.compile(r'\$\{([^{}]+)\}')


def validate(value,template):
    if value is None or value=='':return None
    if not isinstance(value,str) or len(value)>2000 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('自定义结果目录必须是最多2000字符的路径')
    settings=template.get('host_runtime') if template.get('execution_mode')=='host' else template.get('runtime')
    settings=settings or {}
    allowed={'task_name','work_dir','output_dir','cache_dir'}
    fields=(template.get('parameter_schema') or {}).get('fields',{})
    allowed.update('business.'+k for k,v in fields.items() if v.get('type') not in ('object','array','boolean'))
    allowed.update('assets:'+k for k in settings.get('assets',[]))
    allowed.update('mounts:'+k for k in settings.get('mounts',{}))
    if any(k not in allowed for k in TOKEN.findall(value)) or '${' in TOKEN.sub('x',value):
        raise ValueError('自定义结果目录含未知占位符；业务字段须先声明')
    if '..' in Path(TOKEN.sub('x',value)).parts:
        raise ValueError('自定义结果目录不允许父目录穿越')
    if not value.startswith(('/', '${')):raise ValueError('自定义结果目录必须是绝对路径或目录占位符')
    return value


def absolute(value):
    if not isinstance(value,str) or not value or len(value)>4096 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('自定义结果目录解析无效')
    path=Path(value)
    if not path.is_absolute() or str(path)=='/' or '..' in path.parts or '\\' in value:
        raise ValueError('自定义结果目录解析无效')
    return str(path)


def resolve(template,spec,storage,runtime,profile,execution,output):
    """Pure mapping; never inspect remote files or trust a browser-supplied root."""
    declaration=template.get('result_directory')
    validate(declaration,template)
    host=template.get('execution_mode')=='host'
    settings=(template.get('host_runtime') if host else template.get('runtime')) or {}
    workspace=absolute(storage.get('workspace_root'))
    work=str(Path(workspace)/'runs'/execution/'work')
    output=absolute(output or str(Path(workspace)/'runs'/execution/'output'))
    cache=absolute(storage.get('cache_root'))
    assets=runtime.get('assets',{})
    sources={'work':work,'output':output,'cache':cache}
    sources.update({'assets:'+k:v for k,v in assets.items()})
    sources.update({'directories:'+k:v['path'] for k,v in runtime.get('directories',{}).items()})
    values={'task_name':spec.get('task_name','')}
    values.update({'business.'+k:v for k,v in spec.get('parameters',{}).items()})
    if host:
        values.update(work_dir=work,output_dir=output,cache_dir=cache)
        values.update({'assets:'+k:assets[k] for k in settings.get('assets',[]) if k in assets})
        roots=[work,output,cache,profile.get('code_root')]
        roots.extend(assets[k] for k in settings.get('assets',[]) if k in assets)
        roots.extend(v['path'] for v in runtime.get('directories',{}).values())
        mappings=[]
    else:
        values.update(work_dir=settings.get('work_dir','/tmp/mini-eval-work'),output_dir=settings.get('output_dir','/tmp/mini-eval-output'),cache_dir='/opt/mini-eval/cache')
        mappings=[('/tmp/mini-eval-work',work),('/tmp/mini-eval-output',output),('/opt/mini-eval/cache',cache),
                  (values['work_dir'],work),(values['output_dir'],output)]
        for name,target in settings.get('assets',{}).items():
            if name in assets:values['assets:'+name]=target;mappings.append((target,assets[name]))
        for name,item in settings.get('mounts',{}).items():
            if item['source'] in sources:
                values['mounts:'+name]=item['target'];mappings.append((item['target'],sources[item['source']]))
        roots=[]
    def expand(match):
        value=values.get(match[1])
        if not isinstance(value,(str,int,float)) or isinstance(value,bool):
            raise ValueError('自定义结果目录的参数缺失或不是文本/数值')
        return str(value)
    path=Path(absolute(TOKEN.sub(expand,declaration)))
    if host:
        if not any(root and path.is_relative_to(Path(absolute(root))) for root in roots):
            raise ValueError('自定义结果目录不在节点批准的宿主机目录内')
        return str(path)
    candidates=[(len(Path(target).parts),str(Path(absolute(source))/path.relative_to(target)))
                for target,source in mappings if path.is_relative_to(Path(target))]
    if not candidates:raise ValueError('自定义结果目录不在容器的持久挂载内')
    longest=max(n for n,_ in candidates);matches={p for n,p in candidates if n==longest}
    if len(matches)!=1:raise ValueError('自定义结果目录的持久挂载映射有歧义')
    return matches.pop()


def bind(value):
    mapping,relative=shared_storage.match(value['node_name'],value['output_root'])
    return {**value,'storage_id':mapping['id'],'marker':mapping['marker'],'relative':relative}


def plan(s,run,worker,slot,job,fresh):
    if not fresh or s.get(ExecutionBrowseRoot,run.id):return
    spec=job.client_spec or {};template=spec.get('template',{})
    if job.benchmark!='cs-client' or not template.get('result_directory'):return
    from .storage import desired
    from .core import SharedArtifactRoot
    storage=desired(worker,slot)['config'] or (slot.storage_report or {}).get('effective') or {}
    platform=s.get(SharedArtifactRoot,run.id)
    output=str(Path(platform.snapshot['output_root'])/'output') if platform and platform.snapshot.get('layout')=='batch-v1' else None
    profile=(worker.host_environments or {}).get((template.get('host_runtime') or {}).get('environment_name'),{})
    value={'node_name':worker.node_name}
    try:
        value['output_root']=resolve(template,spec,storage,worker.runtime_config or {},profile,run.execution_id,output)
        try:value=bind(value)
        except HTTPException:pass  # A later first mapping may bind the frozen path.
    except (ValueError,KeyError,TypeError):
        value['error']='自定义结果目录无法解析或不在批准的持久目录内，请核对本次模板参数及节点配置'
    s.add(ExecutionBrowseRoot(run_id=run.id,execution_id=run.execution_id,snapshot=value))
    s.flush()


def location(s,run):
    row=s.scalar(select(ExecutionBrowseRoot).where(ExecutionBrowseRoot.run_id==run.id).with_for_update().execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'本次执行未登记自定义结果浏览目录；不会从当前模板补造历史路径')
    if row.execution_id!=run.execution_id:raise HTTPException(409,'自定义结果目录执行身份不一致')
    if row.snapshot.get('error'):raise HTTPException(409,row.snapshot['error'])
    if 'storage_id' not in row.snapshot:
        row.snapshot=bind(row.snapshot);s.commit()
    return row.snapshot
