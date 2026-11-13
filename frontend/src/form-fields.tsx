import React from 'react';

export function Field({label,children}:{label:string;children:React.ReactNode}) {const id=React.useId();return <div className="field"><label htmlFor={id}>{label}</label>{React.cloneElement(children as React.ReactElement<{id:string}>,{id})}</div>}
