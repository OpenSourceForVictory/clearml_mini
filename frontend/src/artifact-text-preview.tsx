import React,{useEffect,useState} from 'react';

const LIMIT=256*1024;
export function textPreviewKind(name:string){
 const suffix=name.split('.').at(-1)?.toLowerCase();
 return suffix==='json'?'json':suffix==='csv'?'csv':['txt','log'].includes(suffix||'')?'text':null;
}

// Bound actual consumed bytes even when a server ignores Range. Never use response.text().
async function readPreview(url:string,signal:AbortSignal){
 const response=await fetch(url,{signal,credentials:'same-origin',headers:{Range:`bytes=0-${LIMIT}`}});
 if(response.status===416&&response.headers.get('content-range')==='bytes */0')return {text:'',truncated:false};
 if(!response.ok)throw new Error(`文件读取失败（HTTP ${response.status}）`);
 if(!response.body)throw new Error('文件响应没有可读取的正文');
 const reader=response.body.getReader(),chunks:Uint8Array[]=[];
 let size=0,ended=false;
 try{
  while(size<=LIMIT){
   const next=await reader.read();
   if(next.done){ended=true;break;}
   const part=next.value.slice(0,LIMIT+1-size);chunks.push(part);size+=part.length;
  }
 }finally{await reader.cancel();}
 const total=response.headers.get('content-range')?.match(/\/(\d+)$/)?.[1];
 const truncated=size>LIMIT||!!total&&Number(total)>LIMIT||!ended;
 const bytes=new Uint8Array(Math.min(size,LIMIT));let offset=0;
 for(const chunk of chunks){const part=chunk.subarray(0,bytes.length-offset);bytes.set(part,offset);offset+=part.length;}
 return {text:new TextDecoder().decode(bytes,{stream:truncated}),truncated};
}

function JsonTree({value,depth=0}:{value:unknown;depth?:number}){
 const [open,setOpen]=useState(depth===0);
 if(value===null||typeof value!=='object')return <code>{JSON.stringify(value)}</code>;
 if(depth>=12)return <span>嵌套层级超过预览上限，请下载查看。</span>;
 const entries=Object.entries(value);
 return <details open={open} onToggle={event=>setOpen(event.currentTarget.open)}><summary>{Array.isArray(value)?'数组':'对象'} · {entries.length} 项</summary>
  {open&&<ul>{entries.slice(0,50).map(([key,item])=><li key={key}><strong>{key}: </strong><JsonTree value={item} depth={depth+1}/></li>)}</ul>}
  {entries.length>50&&<p>仅显示前 50 项，请下载查看完整内容。</p>}
 </details>;
}

// Parse quoted commas/newlines and escaped quotes, limiting retained cells and rows.
function csvRows(text:string){
 const rows:string[][]=[];let row:string[]=[],cell='',quoted=false,limited=false;
 const pushCell=()=>{if(row.length<50)row.push(cell);else limited=true;cell='';};
 for(let i=0;i<text.length;i++){
  const char=text[i];
  if(char==='"'){
   if(quoted&&text[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;
  }else if(!quoted&&(char===','||char==='\n'||char==='\r')){
   pushCell();
   if(char!==','){
    rows.push(row);row=[];if(char==='\r'&&text[i+1]==='\n')i++;
    if(rows.length===200){limited=i<text.length-1||limited;return {rows,limited,malformed:false};}
   }
  }else cell+=char;
 }
 if(cell||row.length){pushCell();rows.push(row);}
 return {rows,limited,malformed:quoted};
}

export function ArtifactTextPreview({url,kind}:{url:string;kind:string}){
 const [data,setData]=useState<{text:string;truncated:boolean}|null>(null),[error,setError]=useState('');
 useEffect(()=>{const controller=new AbortController();setData(null);setError('');
  readPreview(url,controller.signal).then(value=>{if(!controller.signal.aborted)setData(value);}).catch(e=>{if(!controller.signal.aborted)setError(String(e));});
  return()=>controller.abort();
 },[url]);
 if(error)return <p role="alert">{error}。请重新加载预览或下载文件。</p>;
 if(!data)return <p role="status">正在读取预览…</p>;
 let content:React.ReactNode=<pre>{data.text||'（空文件）'}</pre>;
 if(kind==='json'&&!data.truncated){
  try{content=<JsonTree value={JSON.parse(data.text)}/>;}catch{content=<><p role="alert">JSON 解析失败，以下显示原始文本。</p>{content}</>;}
 }else if(kind==='csv'){
  const csv=csvRows(data.text);
  content=<>{csv.malformed&&<p role="alert">CSV 引号未闭合，表格可能不完整。</p>}{csv.limited&&<p>表格最多预览 200 行、50 列，请下载查看完整内容。</p>}
   <div className="table-scroll"><table aria-label="CSV 预览"><tbody>{csv.rows.map((row,i)=><tr key={i}>{row.map((cell,j)=><td key={j}>{cell}</td>)}</tr>)}</tbody></table></div></>;
 }
 return <div className="artifact-text">{data.truncated&&<p role="status">仅预览前 256 KiB，内容已截断，请下载查看完整文件。</p>}{content}</div>;
}
