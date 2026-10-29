"""Shared standard-library helpers. SPDX-License-Identifier: GPL-3.0-only"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

class Error(Exception):
    pass

def require(ok,message):
    if not ok:raise Error(message)

def sha(data):return hashlib.sha256(data).hexdigest()

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb',buffering=0) as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def run(args,**kwargs):
    try:
        return subprocess.run([str(a) for a in args],check=True,text=True,
                              capture_output=True,timeout=kwargs.pop('timeout',180),**kwargs).stdout
    except (OSError,subprocess.CalledProcessError) as e:
        raise Error(str(e)+(' '+e.stderr if isinstance(e,subprocess.CalledProcessError) else '')) from e

def atomic(path,data,mode=0o600):
    path=Path(path);tmp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if tmp.exists():tmp.unlink()

def save_json(path,data):atomic(path,(json.dumps(data,indent=2)+'\n').encode())
