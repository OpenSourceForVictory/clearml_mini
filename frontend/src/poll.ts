import {createContext,useContext,useEffect,useState} from 'react';

export const PollVisibility=createContext(true);
import {api} from './api';

// Schedule after completion: slow requests cannot overlap or overwrite a new route.
export function usePoll<T>(path:string, enabled=true) {
  const [data,setData]=useState<T|null>(null),[error,setError]=useState('');
  const visible=useContext(PollVisibility);
  useEffect(()=>{setData(null);setError('');},[path]);
  useEffect(()=>{
    let active=true,timer:ReturnType<typeof setTimeout>;
    async function load(){
      try{const value=await api<T>(path);if(active){setData(value);setError('');}}
      catch(e){if(active)setError(String(e));}
      finally{if(active)timer=setTimeout(load,2000);}
    }
    if(enabled&&visible)void load();
    return()=>{active=false;clearTimeout(timer);};
  },[path,enabled,visible]);
  return {data,error};
}
