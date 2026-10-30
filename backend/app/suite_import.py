"""Bounded, data-only suite documents. No environment or network checks."""
import hashlib
import json
import math
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .batches import Case
from .clients import ClientRequest

MAX_BYTES = 1048576


def pairs(items):
    result = {}
    for key, value in items:
        if not isinstance(key, str):
            raise ValueError('mapping keys must be strings')
        if key in result:
            raise ValueError('duplicate key: '+key[:100])
        result[key] = value
    return result


class Loader(yaml.SafeLoader):
    pass


def mapping(loader, node):
    return pairs([(loader.construct_object(k), loader.construct_object(v)) for k, v in node.value])


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def json_tree(value, depth=0, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 24 or budget[0] > 20000:
        raise ValueError('document nesting or node count exceeds limit')
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError('mapping keys must be strings')
        for v in value.values():
            json_tree(v, depth+1, budget)
    elif isinstance(value, list):
        for v in value:
            json_tree(v, depth+1, budget)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError('only JSON-compatible values are supported; quote dates and versions')
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('non-finite numbers are not supported')


def parse(text):
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('document exceeds 1 MiB')
    text = text.lstrip('\ufeff')
    if text.lstrip().startswith(('[', '{')):
        value = json.loads(text, object_pairs_hook=pairs)
    else:
        depth = count = 0
        for event in yaml.parse(text, Loader=Loader):
            count += 1
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, 'anchor', None):
                raise ValueError('YAML anchors and aliases are not supported')
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
            if isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1
            if depth > 24 or count > 40000:
                raise ValueError('document nesting or node count exceeds limit')
        value = yaml.load(text, Loader=Loader)
    json_tree(value)
    return value


class ClientHint(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(max_length=200)
    version: str = Field(max_length=100)


class Defaults(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    episodes: int = Field(default=3, ge=1, le=10000)
    seed: int = Field(default=0, ge=0, le=2147483647)
    parameters: dict = Field(default_factory=dict)


class Document(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    name: str = Field(default='', max_length=200)
    version: str = Field(default='1', max_length=100)
    description: str = Field(default='', max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source: str = Field(default='', max_length=1000)
    client: ClientHint | None = None
    defaults: Defaults = Field(default_factory=Defaults)
    cases: list = Field(min_length=1, max_length=100)


def errors(exc, prefix=''):
    return [{'field': prefix+'.'.join(map(str, e['loc'])), 'message': e['msg']} for e in exc.errors()[:100]]


def normalize(text):
    try:
        value = parse(text)
        doc = Document.model_validate({'cases': value} if isinstance(value, list) else value)
        if any(not t.strip() or len(t)>40 for t in doc.tags):
            raise ValueError('tags must contain 1–40 characters')
        ClientRequest.bounded_parameters(doc.defaults.parameters)
    except ValidationError as e:
        return {'valid': False, 'errors': errors(e)}
    except (ValueError, yaml.YAMLError, RecursionError, TypeError) as e:
        return {'valid': False, 'errors': [{'field':'document', 'message':str(e)[:500]}]}
    cases, issues, ids = [], [], set()
    explicit = {x.get('case_id') for x in doc.cases if isinstance(x, dict) and isinstance(x.get('case_id'), str)}
    for i, raw in enumerate(doc.cases):
        try:
            if not isinstance(raw, dict):
                raise ValueError('task must be an object')
            params = raw.get('parameters', {})
            if not isinstance(params, dict):
                raise ValueError('parameters must be an object')
            data = {**doc.defaults.model_dump(), **raw, 'parameters':{**doc.defaults.parameters, **params}}
            if 'case_id' not in data:
                candidate = f'case-{i+1:03d}'
                while candidate in explicit or candidate in ids:
                    candidate += '-auto'
                data['case_id'] = candidate
            case = Case.model_validate(data, strict=True)
            ClientRequest.bounded_parameters(case.parameters)
            if case.case_id in ids:
                raise ValueError('duplicate case_id')
            ids.add(case.case_id)
            cases.append(case.model_dump())
        except ValidationError as e:
            issues.extend(errors(e, f'cases.{i}.'))
        except ValueError as e:
            issues.append({'field':f'cases.{i}', 'message':str(e)[:500]})
    if issues:
        return {'valid':False, 'errors':issues[:100]}
    result = doc.model_dump(exclude={'defaults'})
    result['cases'] = cases
    return {'valid':True,'errors':[], 'document':result,'task_count':len(cases),
            'total_episodes':sum(c['episodes'] for c in cases),'content_hash':digest(cases)}


def digest(cases):
    return hashlib.sha256(json.dumps(cases,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def catalog():
    return dict(schema_version=1,name='RoboTwin 2.0',version='1',description='50 个固定版本任务，可选择子集。默认 demo_clean / joint。',
        tags=['RoboTwin'],source='RoboTwin ea8b21121ebb3cd201ff5b3fe361944ac94eda3f / env_cfg/eval/all_tasks.yml',
        cases=json.loads((Path(__file__).parent/'catalogs/robotwin2.json').read_text()))
