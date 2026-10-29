#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Manual prefix install/restore, preserving GPT, UFS state and boot B."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import time
from common import require,digest,save_json
from device import snapshot,same_device

def write_file(fd,source,limit):
    offset=0
    with open(source,'rb') as f:
        while b:=f.read(1024*1024):
            require(offset+len(b)<=limit,'Write exceeds approved extent')
            done=0
            while done<len(b):
                n=os.pwrite(fd,b[done:],offset+done);require(n>0,'Short boot write');done+=n
            offset+=len(b)
    os.fsync(fd)
    return offset

def transactional_write(node,target,expected,before_image,source_expected,limit):
    """Only boot A is opened writable; every reported write error restores it."""
    fd=os.open(node,os.O_RDWR|os.O_CLOEXEC)
    try:
        try:
            write_file(fd,target,limit)
            require(digest(node)==expected,'Full boot-A readback failed')
        except BaseException:
            write_file(fd,before_image,limit)
            require(digest(node)==source_expected,'ROLLBACK FAILED: keep the running session alive and use its backups')
            raise
    finally:os.close(fd)

def main(a):
    capture=a.capture.resolve();candidate=a.candidate.resolve()
    base=json.loads((capture/'capture.json').read_text());r=json.loads((candidate/'report.json').read_text())
    require(r['source_capture_sha256']==digest(capture/'capture.json'),'Candidate belongs to another capture')
    require(r.get('wrapper_qa_passed') is True,'Run tools/verify_arm64.py successfully before installation')
    require(not r['slot_switch_enabled'],'Public installer handles the normal menu only; see advanced/slot-switch')
    require(r['image_sha256']==digest(candidate/'selector.img'),'Candidate image changed')
    for s in 'ab':require(digest(capture/('boot_'+s+'.img'))==base['firmware_hashes']['boot_'+s],'Recovery backup changed')
    image=candidate/'selector.img'
    require(image.stat().st_size==r['image_bytes']<=base['partitions']['boot_a']['bytes'],'Invalid candidate size')
    source_a=r['full_partition_sha256'] if a.restore else base['firmware_hashes']['boot_a']
    before=snapshot();same_device(before,base,source_a)
    require(r['original_android_boot_sha256']==base['firmware_hashes']['boot_a'],'Android source is not the captured A image')
    if not (a.install or a.restore):
        print('Checks passed. No boot write made. Use --install only after both RAM boot tests.');return
    operation=capture/'operations'/str(time.time_ns());operation.mkdir(parents=True,mode=0o700)
    preimage=operation/'boot_a_before.img'
    with open(before['partitions']['boot_a']['path'],'rb',buffering=0) as src,preimage.open('xb') as dst:
        shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
    require(digest(preimage)==source_a,'Pre-write recovery copy failed')
    save_json(operation/'before.json',before)
    target=capture/'boot_a.img' if a.restore else image
    expected=base['firmware_hashes']['boot_a'] if a.restore else r['full_partition_sha256']
    node=before['partitions']['boot_a']['path'];limit=before['partitions']['boot_a']['bytes']
    try:
        transactional_write(node,target,expected,preimage,source_a,limit)
        same_device(snapshot(),base,expected)
    except BaseException:
        # Also roll back a failure detected by the complete post-write audit.
        fd=os.open(node,os.O_RDWR|os.O_CLOEXEC)
        try:write_file(fd,preimage,limit)
        finally:os.close(fd)
        require(digest(node)==source_a,'ROLLBACK FAILED; keep this session running')
        same_device(snapshot(),base,source_a)
        raise
    save_json(operation/'result.json',{'operation':'restore' if a.restore else 'install','boot_a_sha256':expected,'boot_b_unchanged':True,'gpt_unchanged':True,'updater_installed':False})
    print('Verified. Boot B and GPT metadata are unchanged. No reboot was requested.')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--capture',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True)
    g=p.add_mutually_exclusive_group();g.add_argument('--install',action='store_true');g.add_argument('--restore',action='store_true')
    main(p.parse_args())
