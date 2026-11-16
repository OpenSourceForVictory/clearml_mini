import React,{useEffect,useState} from 'react';
import {api} from './api';
import {SuiteManager} from './suites';
import {ImageClients} from './image-runtime';
import {ClientSources} from './cs';
import {TemplateLibrary} from './template-library';
import {FunctionPages} from './navigation';
import type {Template,Suite} from './run-wizard';
export function EvaluationSetup({onRun,onJob,member=false}:{onRun:(suite:Suite)=>void;onJob:(id:string)=>void;member?:boolean}){
 const [templates,setTemplates]=useState<Template[]>([]),[error,setError]=useState(''),[tab,setTab]=useState('suites');
 const load=()=>api<Template[]>('client-templates').then(t=>setTemplates(t.filter(x=>x.enabled)));
 useEffect(()=>{void load().catch(e=>setError(String(e)))},[]);
 const suites=<><h2>评测集合管理</h2>{error&&<p role="alert">{error}</p>}<SuiteManager templates={templates} onRun={onRun} onJob={onJob} onSaved={()=>void load().catch(e=>setError(String(e)))}/></>;
 return member?<div className="administration"><div className="page-title"><h1>评测配置</h1></div><FunctionPages label="评测配置" value={tab} onChange={setTab} items={[{id:'suites',label:'评测集合管理',content:suites},{id:'clients',label:'客户端接入',content:<ClientRegistration/>}]}/></div>:suites;
}
export function ClientRegistration(){
 const [tab,setTab]=useState('image'),[revision,setRevision]=useState(0);
 return <FunctionPages label="客户端接入" value={tab} onChange={setTab} items={[
 {id:'image',label:'镜像接入',content:<ImageClients onChange={()=>setRevision(v=>v+1)}/>},
 {id:'source',label:'代码构建',content:<ClientSources/>},
 {id:'library',label:'我的模板版本',content:<TemplateLibrary key={revision} onChange={()=>{}}/>}
 ]}/>;
}
