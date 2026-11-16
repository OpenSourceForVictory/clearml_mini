"""Register sample templates/suite and optionally submit batches to an upgraded API."""
import argparse,json,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from definitions import template,cases


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api',required=True)
    parser.add_argument('--token-file',type=Path,required=True,help='file containing an administrator access token')
    parser.add_argument('--pool-id',required=True)
    parser.add_argument('--server-pool-id')
    parser.add_argument('--mode',choices=['host','docker'],default='docker')
    parser.add_argument('--image',default='mini-eval-scheduling-acceptance:v1')
    parser.add_argument('--service-mode',choices=['shared','exclusive'],default='shared')
    parser.add_argument('--submit-count',type=int,default=0,help='0 registers only; use 3 for queue validation')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 0<=args.submit_count<=10:parser.error('submit-count must be 0..10')
    if args.output.exists():parser.error('output exists; retain it to avoid duplicate registration')
    token=args.token_file.read_text().strip();version='acceptance-'+uuid.uuid4().hex[:10]
    record={'version':version,'templates':{},'batches':[]}
    def save():args.output.write_text(json.dumps(record,indent=2)+'\n')
    def api(path,body,method='POST',key=None):
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
        if key:headers['Idempotency-Key']=key
        request=Request(args.api.rstrip('/')+'/api/v1/'+path,data=json.dumps(body).encode(),headers=headers,method=method)
        with urlopen(request,timeout=30) as response:return json.load(response)
    save()
    for kind in ('client','server'):
        result=api('admin/'+kind+'-templates',template(kind,args.mode,args.image,version))
        record['templates'][kind]=result['id'];save()
    suite=api('suites',{'name':'Scheduling acceptance: three cases','version':version,'template_id':record['templates']['client'],'cases':cases()})
    record['suite_id']=suite['id'];save()
    body={'title':version,'suite_id':suite['id'],'pool_id':args.pool_id,'max_concurrent':2,
          'client':{'template_id':record['templates']['client'],'service_mode':'managed'},
          'services':[{'template_id':record['templates']['server'],'pool_id':args.server_pool_id or args.pool_id,'mode':args.service_mode,'max_instances':1,'parameters':{'model':'test-model','label':'server-label'}}]}
    record['preflight']=api('orchestrations/preflight',body);save()
    for i in range(args.submit_count):
        result=api('orchestrations',{**body,'title':version+'-'+str(i)},key=version+'-'+str(i))
        record['batches'].append(result['id']);save()
    print(json.dumps({'record':str(args.output),'suite_id':record['suite_id'],'batches':record['batches']}))


if __name__=='__main__':main()
