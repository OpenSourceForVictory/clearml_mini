"""Weighted coverage summaries. Functional groups overlap; directory totals do not."""
import argparse
import fnmatch
import json
from pathlib import Path

DIRECTORIES = {
    'backend/app': 'API 与调度', 'worker': 'Worker 执行',
    'node_agent': '节点 Agent', 'runner': '业务 Runner / SDK',
    'onboarding': 'SSH 纳管', 'scripts': '部署与运维脚本',
}
FUNCTIONS = {
    '调度与资源': ['backend/app/db_budget.py','backend/app/priority_runtime.py','backend/app/priority_routes.py','backend/app/priority_gateway.py','backend/app/scheduling.py','backend/app/node_resources.py','backend/app/job_resources.py','backend/app/orchestration_scheduler.py'],
    '节点纳管与升级': ['onboarding/*.py','backend/app/node*.py','backend/app/worker_upgrades.py','scripts/install*.py','scripts/node_layout.py'],
    '业务执行与结果': ['backend/app/recovery_records.py','backend/app/recovery_results.py','backend/app/recovery_finish.py','backend/app/recovery_claims.py','backend/app/recovery_launch.py','worker/recovery_launch.py','worker/recovery_claim.py','backend/app/recovery_control.py','worker/recovery_delivery.py','worker/recovery_finish.py','worker/recovery_poll.py','worker/recovery_start.py','worker/recovery_monitor.py','worker/recovery_control.py','worker/recovery_supervisor.py','worker/recovery_runtime.py','worker/disconnection.py','worker/reliable_outbox.py','worker/backpressure.py','backend/app/execution.py','backend/app/execution_timing.py','backend/app/episodes.py','backend/app/result_directories.py','backend/app/log_policy.py','worker/log_policy.py','worker/artifact_layout.py','backend/app/shared_storage.py','backend/app/shared_file_io.py','backend/app/shared_artifacts.py','worker/artifact_reader.py','worker/artifact_access.py','backend/app/artifact_media.py','backend/app/process_logs.py','backend/app/execution_locations.py','backend/app/result_views.py','backend/app/read_views.py','worker/host_backend.py','worker/client.py','worker/log_stream.py','worker/process_log_reader.py','worker/process_logs.py','backend/app/execution_logs.py','runner/*.py'],
    '节点监控': ['node_agent/*.py'],
    '运行模板注册管理': ['backend/app/clients.py','backend/app/templates.py','backend/app/template_versions.py','runner/template_contract.py','runner/image_runtime.py'],
    '评测结果与批次管理': ['backend/app/reports.py','backend/app/batch_controls.py','backend/app/batch_deletion.py','backend/app/monitoring.py','backend/app/read_views.py'],
    '任务集合与编排': ['backend/app/batch_rerun.py','backend/app/suites.py','backend/app/batches.py','backend/app/orchestrations.py','backend/app/services.py'],
}


def percentage(covered, total):
    return 100.0 * covered / total if total else None


def aggregate(files):
    result={'files':len(files),'lines':0,'covered_lines':0,'branches':0,'covered_branches':0}
    for value in files.values():
        s=value['summary']
        for target,source in [('lines','num_statements'),('covered_lines','covered_lines'),('branches','num_branches'),('covered_branches','covered_branches')]:
            result[target]+=s[source]
    result['line_percent']=percentage(result['covered_lines'],result['lines'])
    result['branch_percent']=percentage(result['covered_branches'],result['branches'])
    return result


def summarize(data, floors):
    if not data.get('meta',{}).get('branch_coverage'):
        raise ValueError('branch-enabled coverage JSON is required')
    files={name.replace('\\','/').removeprefix('./'):value for name,value in data['files'].items()}
    selected={name:value for name,value in files.items() if any(name.startswith(d+'/') for d in DIRECTORIES)}
    if len(selected)!=len(files):raise ValueError('coverage paths must be repository-relative and inside configured source directories')
    directories={d:aggregate({n:v for n,v in files.items() if n.startswith(d+'/')}) for d in DIRECTORIES}
    functions={label:aggregate({n:v for n,v in files.items() if any(fnmatch.fnmatchcase(n,p) for p in patterns)}) for label,patterns in FUNCTIONS.items()}
    violations=[]
    if set(floors.get('directories',{}))!=set(DIRECTORIES) or set(floors.get('functions',{}))!=set(FUNCTIONS):
        raise ValueError('floors must define every measured directory and function')
    groups=[*(('directories',name,s) for name,s in directories.items()),*(('functions',name,s) for name,s in functions.items())]
    for kind,directory,summary in groups:
        for metric in ('line','branch'):
            floor=floors[kind][directory][metric]
            if type(floor) not in (int,float) or not 0<=floor<=100:raise ValueError('invalid coverage floor')
            value=summary[metric+'_percent']
            if not summary['files'] or summary['lines']==0:
                violations.append(directory+': source missing from coverage report');break
            if value is not None and value+1e-9<floor:
                violations.append(f'{directory}: {metric} {value:.2f}% < {floor:.2f}%')
    missing={name:{'lines':v.get('missing_lines',[]),'branches':v.get('missing_branches',[])} for name,v in files.items() if v.get('missing_lines') or v.get('missing_branches')}
    return {'scope':'Python only; frontend and uninstrumented subprocesses excluded from claims',
            'total':aggregate(files),'directories':directories,'functions':functions,
            'functional_groups_overlap':True,'requirements':floors,'missing':missing,'violations':violations}


def markdown(report):
    def pct(value):return 'N/A' if value is None else f'{value:.2f}%'
    lines=['# Python 代码覆盖率','',report['scope'],'','| 目录 / 功能 | 文件数 | 行覆盖率 | 分支覆盖率 |','| --- | ---: | ---: | ---: |']
    for label,s in [('全部 Python',report['total']),*report['directories'].items()]:
        lines.append(f"| {label} | {s['files']} | {pct(s['line_percent'])} | {pct(s['branch_percent'])} |")
    lines+=['','## 功能视图（可重叠，不相加）','','| 功能 | 文件数 | 行覆盖率 | 分支覆盖率 |','| --- | ---: | ---: | ---: |']
    for label,s in report['functions'].items():
        lines.append(f"| {label} | {s['files']} | {pct(s['line_percent'])} | {pct(s['branch_percent'])} |")
    lines+=['','## 门槛检查','',*(report['violations'] or ['通过。测试是否成功需单独查看 tests.xml / test-exit.json。'])]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--floors',default='backend/tests/coverage-floors.json');p.add_argument('--output',required=True)
    a=p.parse_args(argv);out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    report=summarize(json.loads(Path(a.input).read_text()),json.loads(Path(a.floors).read_text()))
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    content=markdown(report);(out/'summary.md').write_text(content);print(content)
    return 1 if report['violations'] else 0


if __name__=='__main__':raise SystemExit(main())
