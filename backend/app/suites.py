"""Owner-scoped reusable evaluation suites and immutable run definitions."""
from typing import Literal
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func
from .core import EvaluationSuite, EvaluationBatch, ClientTemplate
from .batches import SuiteCreate, suite_view, batch_view
from .scheduling import lock
from .suite_import import normalize, digest, catalog, json_tree


class SuiteWrite(SuiteCreate):
    status: Literal['draft', 'published'] = 'published'
    description: str = Field(default='', max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source: str = Field(default='', max_length=1000)

    @field_validator('tags')
    @classmethod
    def tags_valid(cls, value):
        if any(not t.strip() or len(t)>40 for t in value):
            raise ValueError('tags must contain 1–40 characters')
        return list(dict.fromkeys(t.strip() for t in value))


class SuiteEdit(SuiteWrite):
    expected_revision: int = Field(ge=0)


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    action: Literal['publish', 'archive', 'restore', 'delete']


class Preview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(max_length=1048576)


def install(app, db, current):
    def owned(s, id, user):
        suite = s.get(EvaluationSuite, id)
        if not suite or suite.owner_id != user.id:
            raise HTTPException(404, 'suite not found')
        return suite

    def validate(s, body, user, exclude=None):
        template = s.get(ClientTemplate, body.template_id)
        if not template or not template.enabled or (template.owner_id is not None and template.owner_id != user.id):
            raise HTTPException(400, 'unknown or disabled client template')
        q = select(EvaluationSuite.id).where(EvaluationSuite.owner_id==user.id, EvaluationSuite.name==body.name, EvaluationSuite.version==body.version)
        if exclude:
            q = q.where(EvaluationSuite.id != exclude)
        if s.scalar(q):
            raise HTTPException(409, 'suite version already exists')
        try:
            json_tree([c.model_dump() for c in body.cases])
        except ValueError as e:
            raise HTTPException(400, str(e))

    def revision(suite, expected):
        if suite.revision != expected:
            raise HTTPException(409, 'suite changed; reload before saving')

    @app.get('/api/v1/suites/catalogs')
    def catalogs(user=Depends(current)):
        return [dict(id='robotwin2', **catalog())]

    @app.post('/api/v1/suites/preview')
    def preview(body: Preview, user=Depends(current), s=Depends(db)):
        result = normalize(body.content)
        if result['valid']:
            result['duplicates'] = [{'id':x.id,'name':x.name,'version':x.version} for x in
                s.scalars(select(EvaluationSuite).where(EvaluationSuite.owner_id==user.id))
                if digest(x.cases)==result['content_hash']][:20]
            hint = result['document'].get('client')
            templates = list(s.scalars(select(ClientTemplate).where(ClientTemplate.enabled,
                (ClientTemplate.owner_id.is_(None)) | (ClientTemplate.owner_id==user.id))))
            matches = [t.id for t in templates if hint and t.name==hint['name'] and t.version==hint['version']]
            result['template_id'] = matches[0] if len(matches)==1 else None
        return result

    @app.get('/api/v1/suites/manage')
    def manage(page:int=Query(1,ge=1), q:str=Query('',max_length=200), tag:str=Query('',max_length=40),
               template_id:str=Query('',max_length=36), state:Literal['active','draft','published','archived','all']='active',
               user=Depends(current), s=Depends(db)):
        query = select(EvaluationSuite).where(EvaluationSuite.owner_id==user.id)
        if q:
            query=query.where(EvaluationSuite.name.icontains(q, autoescape=True))
        if template_id:
            query=query.where(EvaluationSuite.template_id==template_id)
        if state!='all':
            query=query.where(EvaluationSuite.archived.is_(state=='archived'))
        if state in ('draft','published'):
            query=query.where(EvaluationSuite.status==state)
        # JSON tag comparison remains portable to SQLite; filter before paging.
        rows=list(s.scalars(query.order_by(EvaluationSuite.created_at.desc(),EvaluationSuite.id)))
        if tag:
            rows=[x for x in rows if tag in x.tags]
        items=[]
        for x in rows[(page-1)*20:page*20]:
            item=suite_view(x);item.pop('cases')
            item.update(task_count=len(x.cases),total_episodes=sum(c['episodes'] for c in x.cases))
            latest=s.scalar(select(EvaluationBatch).where(EvaluationBatch.suite_id==x.id).order_by(EvaluationBatch.created_at.desc()).limit(1))
            item['latest_run']=({'id':latest.id,'title':latest.title,'status':batch_view(s,latest)['status']} if latest else None)
            items.append(item)
        return dict(items=items,total=len(rows),page=page,page_size=20)

    @app.post('/api/v1/suites',status_code=201)
    def create(body:SuiteWrite,user=Depends(current),s=Depends(db)):
        lock(s);validate(s,body,user)
        suite=EvaluationSuite(owner_id=user.id,**body.model_dump())
        s.add(suite);s.commit()
        return suite_view(suite)

    @app.get('/api/v1/suites/{id}/export')
    def export(id:str,user=Depends(current),s=Depends(db)):
        x=owned(s,id,user);t=s.get(ClientTemplate,x.template_id)
        return dict(schema_version=1,name=x.name,version=x.version,description=x.description,tags=x.tags,source=x.source,
                    client={'name':t.name,'version':t.version},cases=x.cases)

    @app.get('/api/v1/suites/{id}/runs')
    def runs(id:str,page:int=Query(1,ge=1),user=Depends(current),s=Depends(db)):
        owned(s,id,user)
        return [batch_view(s,b) for b in s.scalars(select(EvaluationBatch).where(EvaluationBatch.suite_id==id,
            EvaluationBatch.owner_id==user.id).order_by(EvaluationBatch.created_at.desc()).offset((page-1)*20).limit(20))]

    @app.get('/api/v1/suites/{id}')
    def detail(id:str,user=Depends(current),s=Depends(db)):
        x=owned(s,id,user)
        return {**suite_view(x),'versions':[{'id':v.id,'version':v.version,'status':v.status,'archived':v.archived} for v in
            s.scalars(select(EvaluationSuite).where(EvaluationSuite.owner_id==user.id,EvaluationSuite.name==x.name).order_by(EvaluationSuite.created_at.desc()))]}

    @app.put('/api/v1/suites/{id}')
    def edit(id:str,body:SuiteEdit,user=Depends(current),s=Depends(db)):
        lock(s);x=owned(s,id,user);revision(x,body.expected_revision)
        if x.status!='draft' or x.archived:
            raise HTTPException(409,'only active drafts can be edited; copy to a new version')
        validate(s,body,user,id)
        for k,v in body.model_dump(exclude={'expected_revision'}).items():
            setattr(x,k,v)
        x.revision+=1;s.commit()
        return suite_view(x)

    @app.post('/api/v1/suites/{id}/actions')
    def action(id:str,body:Action,user=Depends(current),s=Depends(db)):
        lock(s);x=owned(s,id,user);revision(x,body.expected_revision)
        if body.action=='delete':
            if x.status!='draft' or s.scalar(select(EvaluationBatch.id).where(EvaluationBatch.suite_id==id).limit(1)):
                raise HTTPException(409,'only unreferenced drafts can be deleted')
            s.delete(x);s.commit();return {'deleted':True}
        if body.action=='publish':
            if x.archived:
                raise HTTPException(409,'restore suite before publishing')
            validate(s,SuiteWrite(**{k:getattr(x,k) for k in SuiteWrite.model_fields}),user,id)
            x.status='published'
        else:
            x.archived=body.action=='archive'
        x.revision+=1;s.commit()
        return suite_view(x)
