import ctypes,json,os,tempfile,sys
from pathlib import Path
from worker.retention import open_path,consume,tree,Protected,remove_tree
libc=ctypes.CDLL(None,use_errno=True)
root=Path(tempfile.mkdtemp());target=root/'execution';target.mkdir();outside=root/'assets';outside.mkdir();(outside/'keep').write_text('protected')
(target/'mounted').mkdir()
assert libc.mount(str(outside).encode(),str(target/'mounted').encode(),None,4096,None)==0,ctypes.get_errno()
fd=open_path(target)
try:
 for name,fn in [('scan',lambda:consume(tree(fd))),('delete',lambda:remove_tree(fd,lambda:None))]:
  try:fn();raise AssertionError('nested same-filesystem bind mount traversed')
  except Protected:print(name+' blocked same-filesystem directory bind mount')
 assert (outside/'keep').read_text()=='protected'
finally:os.close(fd);assert libc.umount2(str(target/'mounted').encode(),0)==0
(target/'mounted').rmdir();(target/'file').write_text('original')
assert libc.mount(str(outside/'keep').encode(),str(target/'file').encode(),None,4096,None)==0,ctypes.get_errno()
fd=open_path(target)
try:
 try:consume(tree(fd));raise AssertionError('file bind mount traversed')
 except Protected:print('scan blocked file bind mount')
finally:os.close(fd);assert libc.umount2(str(target/'file').encode(),0)==0
print(json.dumps({'same_device_bind_mount':'protected','external_asset':'unchanged'}))
