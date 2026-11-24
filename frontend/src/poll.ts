import {createContext,useContext,useEffect,useState} from 'react';
import {api,ApiError} from './api';

export const PollVisibility=createContext(true);

// One request at a time; obsolete or hidden consumers never publish responses.
export function usePoll<T>(path:string, enabled=true, intervalMs=2000) {
  const [data,setData]=useState<T|null>(null),[error,setError]=useState('');
  const visible=useContext(PollVisibility);
  useEffect(()=>{setData(null);setError('');},[path]);
  useEffect(()=>{
    let active=true,timer:ReturnType<typeof setTimeout>|undefined,controller:AbortController|undefined;
    let failures=0,unauthorized=false;
    const allowed=()=>active&&enabled&&visible&&!document.hidden&&!unauthorized;
    const cancel=()=>{clearTimeout(timer);controller?.abort();controller=undefined;};
    async function load(){
      if(!allowed()||controller)return;
      const request=new AbortController();controller=request;
      let delay=intervalMs;
      try{
        const value=await api<T>(path,'GET',undefined,{},request.signal);
        if(allowed()&&!request.signal.aborted){setData(value);setError('');failures=0;}
      }catch(e){
        if(allowed()&&!request.signal.aborted){
          failures=Math.min(failures+1,10);
          delay=Math.min(30000,intervalMs*2**failures+Math.random()*500);
          if(e instanceof ApiError){delay=Math.max(delay,e.retryAfterMs);unauthorized=e.status===401;}
          setError(String(e)+(e instanceof ApiError&&e.reason?`（${e.reason}）`:''));
        }
      }finally{
        if(controller===request){controller=undefined;if(allowed())timer=setTimeout(load,delay);}
      }
    }
    const visibility=()=>{cancel();if(allowed())void load();};
    document.addEventListener('visibilitychange',visibility);
    if(allowed())void load();
    return()=>{active=false;cancel();document.removeEventListener('visibilitychange',visibility);};
  },[path,enabled,visible,intervalMs]);
  return {data,error};
}
