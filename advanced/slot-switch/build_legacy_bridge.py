#!/usr/bin/env python3
"""Host-only native v0 bridge packer for an independently qualified helper.

SPDX-License-Identifier: GPL-3.0-only
No device operations. See README.md before attempting a physical RAM boot.
"""
import argparse
import gzip
import hashlib
from pathlib import Path
import struct
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from common import ROOT,require,run,sha,save_json
from formats import arm64_image,read_cpio,validate_dtb,align
from fdt import properties

def pack_v0(kernel,initrd,cmdline):
    encoded=cmdline.encode();require(0<len(encoded)<1536 and b'\0' not in encoded and b'\n' not in encoded,'Invalid v0 command line')
    header=bytearray(4096);header[:8]=b'ANDROID!'
    struct.pack_into('<10I',header,8,len(kernel),0x8000,len(initrd),0x4000000,0,0,0x1e00000,4096,0,0)
    header[64:64+min(512,len(encoded))]=encoded[:512]
    header[608:608+len(encoded[512:])]=encoded[512:]
    h=hashlib.sha1()
    for payload in (kernel,initrd,b''):h.update(payload);h.update(struct.pack('<I',len(payload)))
    header[576:596]=h.digest()
    return bytes(header)+kernel+bytes(-len(kernel)%4096)+initrd+bytes(-len(initrd)%4096)

def main(a):
    require(not a.output.exists(),'Choose a new output directory')
    kernel=a.kernel.read_bytes();raw,extent,compressed=arm64_image(kernel)
    require(b'Linux version ' in raw,'Expected a native Linux kernel')
    require(len(raw)<=extent<0x3f00000,'Kernel exceeds the qualified legacy RAM layout')
    if not compressed:kernel=gzip.compress(raw,mtime=0)
    initrd=a.initramfs.read_bytes();require(0<len(initrd)<0x2000000,'Initramfs exceeds legacy staging capacity')
    entries={e.name:e for e in read_cpio(initrd)}
    require('init' in entries and 'slot-helper' in entries,'Need the qualified helper initramfs, not an ordinary distro initrd')
    mode=b'--dry-run' if a.mode=='dry' else b'--switch-other'
    require(b'/slot-helper '+mode+b' --root /newroot' in entries['init'].data,'Initramfs mode does not match requested bridge mode')
    require(b'SHENG-SELF-CONTAINED-SLOT-BEGIN' in entries['init'].data,'Explicit early-init helper branch missing')
    helper=entries['slot-helper'].data
    require(helper[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<HH',helper,16)==(2,183),'Need static ARM64 helper executable')
    offset=struct.unpack_from('<Q',helper,32)[0];step,count=struct.unpack_from('<HH',helper,54)
    require(step==56 and offset+step*count<=len(helper),'Invalid helper program headers')
    require(all(struct.unpack_from('<I',helper,offset+i*step)[0] not in (2,3) for i in range(count)),'Dynamic helper not allowed')
    dtb=validate_dtb(a.dtb.read_bytes());old=properties(dtb)
    a.output.mkdir(parents=True);work=a.output/'build';work.mkdir()
    (work/'source.dtb').write_bytes(dtb)
    run([a.dtc,'-I','dtb','-O','dts','-o',work/'source.dts',work/'source.dtb'])
    extra='\n&{/chosen} { sheng,native-boot="menu3-normal"; sheng,slot-switch="other"; /delete-node/ framebuffer; };\n'
    (work/'bridge.dts').write_text((work/'source.dts').read_text()+extra)
    run([a.dtc,'-I','dts','-O','dtb','-o',work/'bridge.dtb',work/'bridge.dts'])
    newdtb=(work/'bridge.dtb').read_bytes();new=properties(newdtb)
    expected={k:v for k,v in old.items() if not k[0].startswith('/chosen/framebuffer')}
    expected[('/chosen','sheng,native-boot')]=b'menu3-normal\0';expected[('/chosen','sheng,slot-switch')]=b'other\0'
    require(new==expected,'Unexpected device-tree change')
    cmdline=a.cmdline_file.read_text().strip()
    combined=kernel+newdtb;image=pack_v0(combined,initrd,cmdline)
    (a.output/'bridge.img').write_bytes(image)
    save_json(a.output/'report.json',{'mode':a.mode,'image_sha256':sha(image),'image_bytes':len(image),'kernel_sha256':sha(raw),'initramfs_sha256':sha(initrd),'helper_sha256':sha(helper),'header_version':0,'stored_images_written':False,'hardware_tested':False,'requires_fresh_host_slot_b_check':True,'requires_independent_firmware_qualification':True})
    print('Built RAM-only bridge:',a.output/'bridge.img')
    print('Do not flash. Require current-slot=b, independent qualification and a passing dry-run before a real bridge boot.')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('kernel','dtb','initramfs','cmdline-file','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--mode',choices=['dry','switch'],default='dry')
    p.add_argument('--dtc',type=Path,default=ROOT/'.build/u-boot/scripts/dtc/dtc');main(p.parse_args())
