import {useEffect,useRef,useState} from 'react';

// Keep list state mounted; restore both keyboard focus and the exact viewport.
export function useDetailNavigation(){
 const [open,setOpen]=useState(false);
 const heading=useRef<HTMLDivElement>(null);
 const origin=useRef<{element:HTMLElement|null;y:number}>({element:null,y:0});
 const dirty=useRef(false);
 useEffect(()=>{if(open){heading.current?.focus({preventScroll:true});heading.current?.scrollIntoView({block:'start'});}},[open]);
 function enter(){if(!open)origin.current={element:document.activeElement as HTMLElement,y:window.scrollY};setOpen(true);}
 function back(){if(!open)return;setOpen(false);requestAnimationFrame(()=>{if(origin.current.element?.isConnected)origin.current.element.focus({preventScroll:true});window.scrollTo({top:origin.current.y,behavior:'instant'});});}
 function replace(){if(dirty.current&&!window.confirm('切换将放弃当前未保存的修改，是否继续？'))return false;return true;}
 return {open,heading,enter,back,replace,dirty,dismiss:()=>setOpen(false)};
}
