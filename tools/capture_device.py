#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Save this tablet's own boot images and metadata. Never writes boot storage."""
import argparse
import os
from pathlib import Path
import shutil
from common import require,digest,save_json
from device import snapshot,same_device

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    require(not a.output.exists(),'Choose a new private output directory')
    before=snapshot();a.output.mkdir(parents=True,mode=0o700)
    for slot in 'ab':
        name='boot_'+slot;target=a.output/(name+'.img')
        require(0<before['partitions'][name]['bytes']<=0x10000000,'Unsupported boot size')
        with open(before['partitions'][name]['path'],'rb',buffering=0) as src,open(target,'xb') as dst:
            shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
        os.chmod(target,0o600)
        require(digest(target)==before['firmware_hashes'][name],'Boot backup readback differs')
    same_device(snapshot(),before)
    save_json(a.output/'capture.json',before)
    print('Captured verified private backups in',a.output)
    print('Keep this directory OFF GitHub. Root owns it; copy/chown it deliberately for transfer.')
