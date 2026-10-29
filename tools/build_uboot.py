#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Fetch the pinned upstream source, apply the board patch, and build U-Boot."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from common import ROOT,require,run,save_json,digest

COMMIT='5508406582f6f7120dce0c4b3059819a41788db2'

def main(a):
    source=ROOT/'.build/u-boot-source';out=ROOT/'.build/u-boot';out.mkdir(parents=True,exist_ok=True)
    if not source.exists():
        run(['git','clone','--depth','1','--branch','v2026.10',a.repository,source],timeout=600)
    require(run(['git','-C',source,'rev-parse','HEAD']).strip()==COMMIT,'Wrong U-Boot revision')
    patch=ROOT/'patches/0001-sheng-platform.patch'
    check=subprocess.run(['git','-C',source,'apply','--check',str(patch)],capture_output=True)
    if check.returncode==0:run(['git','-C',source,'apply',patch])
    else:run(['git','-C',source,'apply','--reverse','--check',patch])
    for f in (ROOT/'src/sheng_menu').glob('*'):
        if f.is_file():shutil.copy2(f,source/'cmd'/f.name)
    ui=source/'cmd/sheng-menu-ui';ui.mkdir(exist_ok=True)
    for n in ['menu-art.h','menu-render.h']:shutil.copy2(ROOT/'assets'/n,ui/n)
    (ui/'build-options.h').write_text('#define SHENG_MENU_SLOT_SWITCH '+str(int(a.experimental_slot_switch))+'\n')
    makefile=source/'cmd/Makefile';text=makefile.read_text();anchor='# SHENG-BOOT-MENU-BEGIN\n'
    if anchor not in text:makefile.write_text(text+'\n'+anchor+'obj-y += sheng_android.o sheng_menu.o sheng_power.o\n# SHENG-BOOT-MENU-END\n')
    environment=(ROOT/'configs/sheng.env').read_text()
    if not a.experimental_slot_switch:
        environment='\n'.join(line for line in environment.splitlines() if not line.startswith(('slotswitch=','bootmenu_5=')))+'\n'
    (source/'board/qualcomm/sheng-test.env').write_text(environment)
    if not (out/'.config').exists():shutil.copy2(ROOT/'configs/sheng.config',out/'.config')
    env=dict(os.environ,SOURCE_DATE_EPOCH='0')
    base=['make','-C',source,'O='+str(out),'CROSS_COMPILE='+a.cross_compile]
    # Inherit the build log; compiler failures remain visible.
    subprocess.run([str(x) for x in base]+['olddefconfig'],env=env,check=True)
    subprocess.run([str(x) for x in base]+['-j'+str(a.jobs)],env=env,check=True)
    config=(out/'.config').read_text()
    for name in ['EFI_LOADER','FASTBOOT_FLASH','UFS','SCSI','DFU','CMD_USB_MASS_STORAGE']:
        require('CONFIG_'+name+'=y' not in config,'Unexpected enabled storage/EFI feature: '+name)
    save_json(out/'build-manifest.json',{'commit':COMMIT,'slot_switch':a.experimental_slot_switch,'u_boot_sha256':digest(out/'u-boot-nodtb.bin'),'patch_sha256':digest(patch),'config_sha256':digest(out/'.config')})
    print('Built',out/'u-boot-nodtb.bin')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--jobs',type=int,default=4)
    p.add_argument('--cross-compile',default='aarch64-linux-gnu-');p.add_argument('--repository',default='https://github.com/u-boot/u-boot.git')
    p.add_argument('--experimental-slot-switch',action='store_true',help='Developer-only UI; requires the independently qualified helper initramfs and private profile')
    a=p.parse_args();require(1<=a.jobs<=128,'Invalid jobs');main(a)
