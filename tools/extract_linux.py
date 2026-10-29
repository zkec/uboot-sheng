#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Extract an owner's known-working native Android-v0 Linux boot image."""
import argparse
import gzip
from pathlib import Path
import struct
import zlib
from common import require,save_json,sha
from formats import arm64_image,read_cpio,validate_dtb,align

def extract(data,out):
    require(len(data)>=4096 and data[:8]==b'ANDROID!','Not an Android boot image')
    ksize,_,rsize,_,ssize,_,_,page,version=struct.unpack_from('<9I',data,8)
    require(version==0 and page==4096 and not ssize,'Only native v0, 4KiB-page images are supported')
    roff=page+align(ksize,page)
    require(64<=ksize<=0x6000000 and 0<rsize<=0x2000000 and roff+rsize<=len(data),'Invalid/truncated native boot layout')
    kernel=data[page:page+ksize];decoder=zlib.decompressobj(31)
    require(kernel.startswith(b'\x1f\x8b'),'Expected native Image.gz with appended DTB')
    raw=decoder.decompress(kernel,0x6000001)
    require(decoder.eof and len(raw)<=0x6000000,'Invalid/oversized kernel gzip stream')
    arm64_image(raw);require(b'Linux version ' in raw,'Kernel is not a recognizable native Linux Image')
    dtb=validate_dtb(decoder.unused_data)
    initrd=data[roff:roff+rsize];entries=read_cpio(initrd)
    require(any(e.name in ('init','./init') for e in entries),'Initramfs has no /init')
    cmdline=(data[64:576]+data[608:1632]).split(b'\0',1)[0].decode()
    require(cmdline.strip(),'Native image has no command line; supply components manually')
    require(not out.exists(),'Output directory already exists');out.mkdir(parents=True)
    for name,value in [('Image.gz',gzip.compress(raw,mtime=0)),('linux.dtb',dtb),('initramfs.gz',initrd)]:
        (out/name).write_bytes(value)
    (out/'cmdline.txt').write_text(cmdline+'\n')
    save_json(out/'inputs.json',{'native_source_sha256':sha(data),'kernel_sha256':sha(raw),'dtb_sha256':sha(dtb),'initramfs_sha256':sha(initrd),'initramfs_unchanged':True})

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--boot',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    extract(a.boot.read_bytes(),a.output);print('Extracted private Linux inputs to',a.output)
