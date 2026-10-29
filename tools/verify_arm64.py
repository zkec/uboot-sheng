#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Execute the real menu wrapper and verify its guarded handoff in ARM64."""
import argparse
import ast
import gc
import hashlib
import json
from pathlib import Path
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
import unicorn.arm64_const as reg

p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);args=p.parse_args()
ROOT=Path(__file__).resolve().parent
out=args.directory.resolve()
r=json.loads((out/'report.json').read_text())
image=(out/'selector.img').read_bytes();kernel=image[4096:4096+r['wrapped_kernel_bytes']]
assert hashlib.sha256(image).hexdigest()==r['image_sha256']
import subprocess
fixture=out/'build/android-test.dtb'
subprocess.run([str(ROOT.parent/'.build/u-boot/scripts/dtc/dtc'),'-I','dts','-O','dtb','-o',str(fixture),str(ROOT.parent/'tests/android-test.dts')],check=True)
tree=fixture.read_bytes()
def parse_fdt(b):
 h=struct.unpack_from('>10I',b);assert h[0]==0xd00dfeed
 pos=h[2];names=b[h[3]:h[3]+h[8]];stack=[];props={};nodes=set()
 while True:
  tag=struct.unpack_from('>I',b,pos)[0];pos+=4
  if tag==1:
   end=b.index(b'\0',pos);name=b[pos:end].decode();pos=(end+4)&~3
   stack.append(name);nodes.add('/'+'/'.join(stack[1:]))
  elif tag==2:stack.pop()
  elif tag==3:
   size,nameoff=struct.unpack_from('>II',b,pos);pos+=8
   name=names[nameoff:].split(b'\0',1)[0].decode();key=('/'+'/'.join(stack[1:]),name)
   assert key not in props;props[key]=b[pos:pos+size];pos=(pos+size+3)&~3
  elif tag==4:pass
  elif tag==9:break
  else:raise AssertionError(tag)
 assert not stack
 reservations=[];off=h[4]
 while True:
  entry=struct.unpack_from('>QQ',b,off);off+=16
  if entry==(0,0):break
  reservations.append(entry)
 return props,nodes,reservations,h[7]
old_props,_,_,_=parse_fdt(tree)
symbols=r['symbols'];offset=r['selector_offset']
uboot=kernel[offset+symbols['uboot_blob']:offset+symbols['uboot_end']]
fit=kernel[offset+symbols['fit_blob']:offset+symbols['fit_end']]
fit_props,_,_,_=parse_fdt(fit)
assert hashlib.sha256(fit_props[('/images/ramdisk','data')]).hexdigest()==r['initramfs_sha256']
import gzip
assert hashlib.sha256(gzip.decompress(fit_props[('/images/kernel','data')])).hexdigest()==r['linux_kernel_sha256']
cases=[dict(name='normal'),dict(name='shifted-image',base=0xa8080000),
       dict(name='el1t',pstate=0x3c4),dict(name='missing-memory',change='memory'),dict(name='bad-magic',change='magic'),
       dict(name='oversized-fdt',change='size'),dict(name='unaligned-fdt',unaligned=True),
       dict(name='foreign-board',change='board'),dict(name='missing-bootargs',change='bootargs'),
       dict(name='missing-splash',change='splash'),dict(name='fdt-in-source',inside=True),
       dict(name='fdt-in-dtb-destination',dt=0xb4000100),
       dict(name='source-overlaps-uboot',base=0xa7000000),
       dict(name='source-overlaps-fit',base=0xad000000),
       dict(name='source-overlaps-dtb',base=0xb2000000)]
results=[]
for case in cases:
    base=case.get('base',0xa8000000);dt=case.get('dt',0xa5000000)
    entry=base+offset;android_entry=base+r['original_entry_offset']
    if case.get('inside'):dt=entry+symbols['dt_scratch']
    original_tree=bytearray(tree)
    change=case.get('change')
    if change=='magic':original_tree[:4]=b'BAD!'
    if change=='size':struct.pack_into('>I',original_tree,4,0x200004)
    for kind,old,new in [('board',b'qcom,kalama\0',b'qcom,kalamz\0'),
                         ('bootargs',b'bootargs\0',b'bootargx\0'),
                         ('splash',b'splash_region\0',b'splash_xxxxxx\0'),
                         ('memory',b'reg\0',b'rex\0')]:
        if change==kind:
            assert old in original_tree and len(old)==len(new)
            original_tree=original_tree.replace(old,new)
    u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.mem_map(0x80000000,0x40000000)
    u.mem_write(base,kernel);u.mem_write(dt,bytes(original_tree))
    initrd=0xb5ff8000;sentinel=b'ANDROID INITRD MUST REMAIN UNCHANGED'
    u.mem_write(initrd,sentinel)
    u.reg_write(reg.UC_ARM64_REG_PSTATE,case.get('pstate',0x3c5))
    initial_sp=0x9f008000;u.reg_write(reg.UC_ARM64_REG_SP,initial_sp)
    initial=[dt+int(case.get('unaligned',False)),0x11223344,0x55667788,0x99aabbcc]
    for i,value in enumerate(initial):u.reg_write(getattr(reg,'UC_ARM64_REG_X'+str(i)),value)
    state={'target':None,'kernel_writes':[]}
    def stop(uc,address,size,target):state['target']=target;uc.emu_stop()
    u.hook_add(UC_HOOK_CODE,stop,'android',begin=android_entry,end=android_entry)
    uboot_entry=base+offset+symbols['uboot_blob']
    u.hook_add(UC_HOOK_CODE,stop,'uboot',begin=uboot_entry,end=uboot_entry)
    def kernel_write(uc,access,address,size,value,user):state['kernel_writes'].append((address,size))
    u.hook_add(UC_HOOK_MEM_WRITE,kernel_write,begin=base,end=base+r['original_kernel_bytes']-1)
    u.emu_start(base,0,timeout=60_000_000,count=500_000_000)
    valid=case['name'] in ('normal','shifted-image','el1t')
    assert state['target']==('uboot' if valid else 'android'),(case,state,hex(u.reg_read(reg.UC_ARM64_REG_PC)))
    assert state['kernel_writes']==[(base+4,4),(base+16,8)],state
    assert hashlib.sha256(u.mem_read(base,r['original_kernel_bytes'])).hexdigest()==r['original_android_kernel_sha256']
    assert bytes(u.mem_read(dt,len(original_tree)))==original_tree
    assert bytes(u.mem_read(initrd,len(sentinel)))==sentinel
    actual=[u.reg_read(getattr(reg,'UC_ARM64_REG_X'+str(i))) for i in range(4)]
    if valid:
        assert actual==[0xb4000000,0,0,0],actual
        assert bytes(u.mem_read(uboot_entry,len(uboot)))==uboot
        fit_addr=base+offset+symbols['fit_blob']
        assert hashlib.sha256(u.mem_read(fit_addr,len(fit))).hexdigest()==r['fit_sha256']
        header=bytes(u.mem_read(0xb4000000,40));size=struct.unpack_from('>I',header,4)[0]
        props,nodes,_,_=parse_fdt(bytes(u.mem_read(0xb4000000,size)))
        assert next(v for (node,key),v in props.items() if node.startswith('/memory') and key=='reg')==old_props[('/memory','reg')]
        assert props[('/chosen','bootargs')]==old_props[('/chosen','bootargs')]
        assert props[('/chosen','sheng,android-fdt')]==struct.pack('>Q',dt)
        assert props[('/chosen','sheng,android-image')]==struct.pack('>Q',base)
        assert props[('/chosen','sheng,android-sha256')]==bytes.fromhex(r['original_android_kernel_sha256'])
        control_props,_,_,_=parse_fdt((out/'build/control.dtb').read_bytes())
        assert props[('/chosen','sheng,linux-bootargs')]==control_props[('/chosen','sheng,linux-bootargs')]
        assert props[('/chosen','linux,initrd-start')]==struct.pack('>Q',fit_addr)
        assert props[('/chosen','linux,initrd-end')]==struct.pack('>Q',fit_addr+len(fit))
        assert '/chosen/framebuffer' in nodes
    else:
        assert actual==initial,(actual,initial)
        assert u.reg_read(reg.UC_ARM64_REG_SP)==initial_sp
    result=dict(case=case['name'],target=state['target'],android_kernel_fdt_initrd_unchanged=True)
    results.append(result);print(result,flush=True)
    del u;gc.collect()
(out/'qa.json').write_text(json.dumps(
    dict(image_sha256=r['image_sha256'],cases=results,hardware_tested=False),indent=2)+'\n')
r['wrapper_qa_passed']=True
(out/'report.json').write_text(json.dumps(r,indent=2)+'\n')
print('PASS',len(results),'actual ARM64 wrapper cases.',flush=True)
