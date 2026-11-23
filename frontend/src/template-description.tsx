import React from 'react';
export function TemplateDescription({template,label}:{template?:{description?:string}|null;label:string}){
 if(!template)return null;
 return <section aria-label={label} style={{gridColumn:'1 / -1',minWidth:0}}><strong>{label}</strong><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{template.description||'暂无模板说明'}</p></section>;
}
