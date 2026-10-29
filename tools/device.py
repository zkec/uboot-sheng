"""Read-only Sheng inventory used by capture and guarded install.

SPDX-License-Identifier: GPL-3.0-only
"""
import os
from pathlib import Path
import re
import struct
import subprocess
import uuid
import zlib
from common import require,digest

CORE='abl aop boot devcfg dtbo hyp init_boot keymaster modem qupfw tz vbmeta vbmeta_system vendor_boot xbl xbl_config xbl_ramdump'.split()
EXTRA='uefi aop_config bluetooth mdtpsecapp mdtp dsp uefisecapp imagefv shrm cpucp featenabler recovery'.split()

def gpt_copies(fd,size):
    require(size%4096==0 and size>=14*4096,'Unsupported disk geometry')
    result=[]
    for lba in (1,size//4096-1):
        h=os.pread(fd,4096,lba*4096)
        require(len(h)==4096 and h[:8]==b'EFI PART' and struct.unpack_from('<II',h,8)==(0x10000,92),'Invalid GPT header')
        hc=bytearray(h[:92]);hc[16:20]=bytes(4)
        require(zlib.crc32(hc)==struct.unpack_from('<I',h,16)[0] and struct.unpack_from('<Q',h,24)[0]==lba and struct.unpack_from('<Q',h,32)[0]==(size//4096-1 if lba==1 else 1),'GPT header CRC/location mismatch')
        start,count,step,crc=struct.unpack_from('<QIII',h,72)
        require(0<count<=128 and step==128 and 2<=start and start*4096+count*step<size-4096,'Invalid GPT entry array')
        t=os.pread(fd,count*step,start*4096)
        require(len(t)==count*step and zlib.crc32(t)==crc,'GPT table CRC mismatch')
        result.append({'header':h.hex(),'table':t.hex()})
    require(result[0]['table']==result[1]['table'],'GPT copies differ; this project does not repair them')
    ph,bh=[bytes.fromhex(c['header']) for c in result]
    require(ph[40:72]==bh[40:72] and ph[80:88]==bh[80:88],'GPT geometry/disk UUID mismatch')
    lo,hi=struct.unpack_from('<QQ',ph,40);require(2<lo<=hi<size//4096-1,'Invalid usable disk bounds')
    regions=[]
    for h in (ph,bh):
        start,count,step=struct.unpack_from('<QII',h,72);regions.append((start*4096,start*4096+count*step))
    require(regions[0][1]<=regions[1][0],'GPT entry arrays overlap')
    ranges=[];table=bytes.fromhex(result[0]['table'])
    for off in range(0,len(table),128):
        e=table[off:off+128]
        if e[:16]==bytes(16):continue
        a,b=struct.unpack_from('<QQ',e,32)
        require(lo<=a<=b<=hi and all(b<x or a>y for x,y in ranges),'Partition bounds overlap or exceed disk')
        require(all(end<=a*4096 or start>=(b+1)*4096 for start,end in regions),'Partition overlaps GPT array')
        ranges.append((a,b))
    return result

def check_misc(data):
    require(len(data)>=32775,'Short misc metadata')
    require(data[:32].split(b'\0',1)[0] in (b'',b'bootonce-bootloader'),'A recovery/fastbootd request is pending')
    version,magic,status,source=struct.unpack_from('<BIBB',data,32768)
    require(version in (1,2) and magic==0x56740ab0 and status in (0,4) and source in (0,1),'Android snapshot merge is pending or its format is unknown')
    return status

def coherent_a(gpt):
    rows={}
    for d in gpt.values():
        t=bytes.fromhex(d['copies'][0]['table'])
        for off in range(0,len(t),128):
            e=t[off:off+128]
            if e[:16]==bytes(16):continue
            name=e[56:].decode('utf-16le').split('\0',1)[0]
            require(re.fullmatch('[A-Za-z0-9_.-]{1,35}',name) and name not in rows,'Invalid/duplicate partition label')
            attrs=struct.unpack_from('<Q',e,48)[0]
            require(bool(attrs&(1<<50))==name.endswith('_a'),'Incoherent ACTIVE flags; refusing metadata repair')
            rows[name]=e
    for n in rows:
        if n.endswith(('_a','_b')):require(n[:-1]+('b' if n.endswith('_a') else 'a') in rows,'Missing paired partition')
    for base in CORE:
        for s in 'ab':require(base+'_'+s in rows,'Missing core partition: '+base+'_'+s)
    a=struct.unpack_from('<Q',rows['boot_a'],48)[0]
    require(a&(1<<54) and not a&(1<<55) and a&(3<<48),'A is not already successful and bootable')
    require(rows['boot_a'][:16]!=rows['boot_b'][:16] and rows['abl_a'][:16]!=rows['abl_b'][:16],'Slot GUIDs are not distinct')
    return rows

def identity():
    require(os.geteuid()==0,'Run this on the tablet with sudo')
    require(not Path('/sys/firmware/efi').exists(),'Use native Linux, not UEFI Linux')
    require(b'xiaomi,sheng' in Path('/proc/device-tree/compatible').read_bytes().split(b'\0'),'This is not xiaomi,sheng')
    require(int(Path('/sys/bus/platform/devices/1d84000.ufshc/attributes/boot_lun_enabled').read_text(),0)==1,'A must already be active; no automatic slot activation is provided')

def snapshot():
    identity();parts={};gpt={}
    for p in sorted(Path('/sys/class/block').iterdir()):
        fields=dict(s.split('=',1) for s in (p/'uevent').read_text().splitlines() if '=' in s)
        if 'PARTNAME' in fields:
            n=fields['PARTNAME'];require(n not in parts,'Duplicate partition')
            parts[n]={'path':str(Path('/dev')/p.name),'partuuid':str(uuid.UUID(fields['PARTUUID'])),'bytes':int((p/'size').read_text())*512,'start':int((p/'start').read_text())*512}
        if not re.fullmatch('sd[a-z]+',p.name):continue
        require(int((p/'queue/logical_block_size').read_text())==4096,'Expected 4KiB UFS blocks')
        size=int((p/'size').read_text())*512;fd=os.open(Path('/dev')/p.name,os.O_RDONLY)
        try:copies=gpt_copies(fd,size)
        finally:os.close(fd)
        guid=str(uuid.UUID(bytes_le=bytes.fromhex(copies[0]['header'])[56:72]))
        require(guid not in gpt,'Duplicate disk UUID');gpt[guid]={'bytes':size,'copies':copies}
    require(len(gpt)==6,'Expected the six-disk Sheng layout')
    rows=coherent_a(gpt)
    for n,e in rows.items():
        lo,hi=struct.unpack_from('<QQ',e,32)
        require(n in parts and parts[n]['partuuid']==str(uuid.UUID(bytes_le=e[16:32])) and parts[n]['bytes']==(hi-lo+1)*4096 and parts[n]['start']==lo*4096,'Kernel/GPT partition mapping differs: '+n)
    require('misc' in parts,'Missing Android misc partition')
    fd=os.open(parts['misc']['path'],os.O_RDONLY)
    try:merge=check_misc(os.pread(fd,65536,0))
    finally:os.close(fd)
    require('linux' in parts,'Expected a Linux partition named linux')
    fd=os.open(parts['linux']['path'],os.O_RDONLY)
    try:sb=os.pread(fd,1024,1024)
    finally:os.close(fd)
    require(sb[56:58]==b'\x53\xef','Linux partition must be ext4')
    fsid=str(uuid.UUID(bytes=sb[104:120]))
    current=subprocess.check_output(['findmnt','-no','UUID','/'],text=True).strip()
    require(current==fsid,'Run from the actual installed Linux filesystem')
    hashes={}
    for base in CORE+EXTRA:
        for s in 'ab':
            n=base+'_'+s
            if n not in parts:continue
            hashes[n]=digest(parts[n]['path'])
    return {'schema':1,'device':'sheng','boot_lun':1,'root_uuid':fsid,'gpt':gpt,'firmware_hashes':hashes,'partitions':parts,'native':True,'snapshot_merge_status':merge}

def same_device(current,baseline,expected_a=None):
    require(current['root_uuid']==baseline['root_uuid'] and current['gpt']==baseline['gpt'],'Disk identity/layout or metadata changed')
    expected=dict(baseline['firmware_hashes'])
    if expected_a:expected['boot_a']=expected_a
    require(current['firmware_hashes']==expected,'A firmware image changed since capture')
    # Node names can reorder after a reboot; compare stable UUIDs and sizes.
    require({n:(p['partuuid'],p['bytes'],p.get('start')) for n,p in current['partitions'].items()}=={n:(p['partuuid'],p['bytes'],p.get('start')) for n,p in baseline['partitions'].items()},'Partition identity changed')
