#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Create a deterministic source-only ZIP, never a firmware release."""
import argparse
import hashlib
from pathlib import Path
import zipfile
from check_public import public_files
from common import ROOT

def release(root,output):
    paths=public_files(root);output.parent.mkdir(parents=True,exist_ok=True)
    manifest=[]
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in paths:
            name=p.relative_to(root).as_posix();data=p.read_bytes()
            item=zipfile.ZipInfo('sheng-boot-menu/'+name,date_time=(1980,1,1,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;item.external_attr=0o100644<<16
            z.writestr(item,data);manifest.append(hashlib.sha256(data).hexdigest()+'  '+name)
        item=zipfile.ZipInfo('sheng-boot-menu/SOURCE-MANIFEST.sha256',date_time=(1980,1,1,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;item.external_attr=0o100644<<16
        z.writestr(item,'\n'.join(manifest)+'\n')
    return len(paths)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'dist/sheng-boot-menu-source.zip');a=p.parse_args()
    count=release(ROOT,a.output);print('Created',a.output,'with',count,'source files. No private inputs or build products.')
