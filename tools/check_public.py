#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Validate the exact source-release file set, excluding private/build data."""
import os
from pathlib import Path
import re
import subprocess
from common import ROOT,require

TOP={'README.md','LICENSE','NOTICE','Makefile','.gitignore','requirements-test.txt'}
DIRS={'docs','tools','src','assets','configs','patches','vendor','LICENSES','tests','advanced','.github'}
SKIP={'.git','.build','.venv','private','dist','__pycache__'}
EXT={'.md','.txt','.py','.c','.h','.S','.ld','.patch','.config','.env','.svg','.png','.yml','.yaml','.dts','.sh'}

def public_files(root):
    root=Path(root);result=[]
    git=subprocess.run(['git','-C',str(root),'rev-parse','--show-toplevel'],capture_output=True,text=True)
    if git.returncode==0 and Path(git.stdout.strip()).resolve()==root.resolve():
        tracked=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
        for name in filter(None,tracked):
            require(not any(part in SKIP for part in Path(name).parts),'Private/build file is tracked in Git: '+name)
    for parent,dirs,files in os.walk(root):
        rel=Path(parent).relative_to(root)
        dirs[:]=sorted(d for d in dirs if d not in SKIP and not (str(rel)=='advanced/slot-switch' and d=='bin'))
        for d in dirs:require(not (Path(parent)/d).is_symlink(),'Symlink directory in source tree')
        for name in sorted(files):
            p=Path(parent)/name;r=p.relative_to(root)
            # Generated inside source ZIPs; regenerate rather than hash itself.
            if str(r)=='SOURCE-MANIFEST.sha256':continue
            if name.endswith(('.pyc','.pyo')):continue
            require(not p.is_symlink(),'Symlink in public source: '+str(r))
            require(r.parts[0] in DIRS or str(r) in TOP,'Unexpected top-level file: '+str(r))
            require(str(r) in TOP or p.suffix in EXT or name in {'LICENSE','NOTICE','Makefile','README','GPL','BSD-2-Clause'},'Unexpected/binary artifact: '+str(r))
            require(p.stat().st_size<2*1024*1024,'Oversized source file: '+str(r))
            b=p.read_bytes()
            for magic in (b'ANDROID!',b'\x7fELF',b'\x1f\x8b',b'\x28\xb5\x2f\xfd'):
                require(not b.startswith(magic),'Firmware or executable artifact: '+str(r))
            for secret in (b'-----BEGIN OPENSSH '+b'PRIVATE KEY-----',b'-----BEGIN RSA '+b'PRIVATE KEY-----',b'-----BEGIN '+b'PRIVATE KEY-----'):
                require(secret not in b,'Private key detected: '+str(r))
            if p.suffix!='.png':
                require(b'/home'+b'/' not in b,'Local home path detected: '+str(r))
                require(not re.search(rb'\b192\.168\.\d+\.\d+\b',b),'Private LAN address detected: '+str(r))
                require(not re.search(rb'ssh-(?:ed25519|rsa) [A-Za-z0-9+/]{20,}',b),'SSH key detected: '+str(r))
            result.append(p)
    require(result,'Empty source tree')
    return sorted(result,key=lambda p:str(p.relative_to(root)))

if __name__=='__main__':
    paths=public_files(ROOT)
    print('PASS:',len(paths),'public source files;',sum(p.stat().st_size for p in paths),'bytes. Private/build/environment/output directories excluded.')
