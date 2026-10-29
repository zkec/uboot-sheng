"""Synthetic GPTs for tests; contains no real disk or filesystem identifiers."""
import struct
import uuid
import zlib

NS=uuid.NAMESPACE_DNS
FS_UUID=str(uuid.uuid5(NS,'sheng-boot-menu.invalid/test-filesystem'))

def baseline():
    core='abl aop boot devcfg dtbo hyp init_boot keymaster modem qupfw tz vbmeta vbmeta_system vendor_boot xbl xbl_config xbl_ramdump'.split()
    groups=[['linux','misc'],['xbl_a','xbl_config_a','xbl_ramdump_a'],
            ['xbl_b','xbl_config_b','xbl_ramdump_b'],
            [b+'_'+s for b in core if b not in ('boot','xbl','xbl_config','xbl_ramdump') for s in 'ab'],
            ['boot_a','boot_b'],['test_unused']]
    result={};size=8*1024*1024;last=size//4096-1
    for number,names in enumerate(groups):
        guid=uuid.uuid5(NS,f'sheng-boot-menu.invalid/disk/{number}')
        table=bytearray(4096)
        for i,name in enumerate(names):
            base=name[:-2] if name.endswith(('_a','_b')) else name
            kind=base if base.startswith('xbl') else name
            e=bytearray(128);e[:16]=uuid.uuid5(NS,'test-type/'+kind).bytes_le
            e[16:32]=uuid.uuid5(NS,'test-part/'+name).bytes_le
            struct.pack_into('<QQ',e,32,32+i*16,47+i*16)
            attrs=(1<<50) if name.endswith('_a') else 0
            if name in ('boot_a','boot_b'):attrs|=(1<<54)|((3 if name.endswith('_a') else 2)<<48)
            struct.pack_into('<Q',e,48,attrs);label=name.encode('utf-16le');e[56:56+len(label)]=label
            table[i*128:(i+1)*128]=e
        copies=[]
        for copy in range(2):
            h=bytearray(4096);h[:8]=b'EFI PART';struct.pack_into('<II',h,8,0x10000,92)
            struct.pack_into('<QQQQ',h,24,last if copy else 1,1 if copy else last,6,last-5)
            h[56:72]=guid.bytes_le;struct.pack_into('<QIII',h,72,last-4 if copy else 2,32,128,zlib.crc32(table))
            struct.pack_into('<I',h,16,zlib.crc32(h[:92]));copies.append({'header':h.hex(),'table':table.hex()})
        result[str(guid)]={'bytes':size,'copies':copies}
    return {'gpt':result}
