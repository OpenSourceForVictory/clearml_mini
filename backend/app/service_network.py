"""Administrator-owned advertisement and port policy; allocation is DB-locked."""
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from sqlalchemy import select
from .core import Worker,ServiceInstance
from .services import Deploy,held
from .scheduling import lock


class Network(BaseModel):
    model_config=ConfigDict(extra='forbid')
    address:str
    bind_address:str|None=None
    port_start:int=Field(default=19000,ge=1024,le=65535)
    port_end:int=Field(default=19100,ge=1024,le=65535)

    @field_validator('address')
    @classmethod
    def reachable(cls,value):return Deploy.host(value)

    @field_validator('bind_address')
    @classmethod
    def local_address(cls,value):
        if value is None:return None
        import ipaddress
        ip=ipaddress.ip_address(value)
        if ip.version!=4 or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValueError('use an explicit local IPv4 address, including loopback for forwarding')
        return str(ip)

    @model_validator(mode='after')
    def bounded(self):
        if self.port_end<self.port_start or self.port_end-self.port_start>1000:raise ValueError('port range must contain 1–1001 ports')
        return self


class Update(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0)
    config:Network|None=None


def choose_port(s,worker,avoid=()):
    config=worker.service_network
    if not config:return None
    used={i.port for i in s.scalars(select(ServiceInstance).where(ServiceInstance.worker_id==worker.id)) if held(s,i)}|set(avoid)
    return next((p for p in range(config['port_start'],config['port_end']+1) if p not in used),None)


def install(app,db,admin):
    @app.get('/api/v1/admin/nodes/{identity}/service-network')
    def get(identity:str,user=Depends(admin),s=Depends(db)):
        w=s.get(Worker,identity)
        if not w:raise HTTPException(404,'node not found')
        return {'revision':w.service_network_revision,'config':w.service_network}

    @app.put('/api/v1/admin/nodes/{identity}/service-network')
    def put(identity:str,body:Update,user=Depends(admin),s=Depends(db)):
        lock(s);w=s.get(Worker,identity)
        if not w:raise HTTPException(404,'node not found')
        if w.service_network_revision!=body.expected_revision:raise HTTPException(409,'service network changed; refresh')
        if body.config and 'server' not in w.roles:raise HTTPException(400,'node must have server role')
        w.service_network=body.config.model_dump() if body.config else None;w.service_network_revision+=1
        from .administration import audit
        audit(s,user,'service-network.update',w.id);s.commit()
        return {'revision':w.service_network_revision,'config':w.service_network}
