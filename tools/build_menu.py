#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build a private native Android/Linux menu from the owner's verified inputs.

No firmware, kernel, initramfs, UUID or image hash is supplied by this repository.
This host tool makes files only. It never writes a tablet's boot storage.
"""
import argparse
import difflib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
from common import Error
from formats import CpioEntry, android_boot, read_cpio, write_cpio, arm64_image, validate_dtb
from fdt import prepare_control


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, data):
    Path(path).write_bytes(data)


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, text=True,
                          capture_output=True, timeout=kwargs.pop('timeout', 180), **kwargs).stdout


def parse_fdt(data):
    """Read bounded FDT properties without running the verifier's CLI."""
    require(len(data) >= 40, 'Short FDT')
    h = struct.unpack_from('>10I', data)
    require(h[0] == 0xd00dfeed and 40 <= h[1] <= len(data), 'Invalid FDT header')
    total, pos, strings_start = h[1], h[2], h[3]
    end = pos + h[9]
    require(40 <= pos < end <= total and strings_start + h[8] <= total, 'Invalid FDT ranges')
    strings = data[strings_start:strings_start+h[8]]
    stack, props = [], {}
    while pos + 4 <= end:
        tag = struct.unpack_from('>I', data, pos)[0]; pos += 4
        if tag == 1:
            nul = data.find(b'\0', pos, end)
            require(nul >= pos, 'Unterminated FDT node')
            stack.append(data[pos:nul].decode())
            pos = (nul + 4) & ~3
        elif tag == 2:
            require(stack, 'Unbalanced FDT node')
            stack.pop()
        elif tag == 3:
            require(pos+8 <= end, 'Short FDT property')
            size, offset = struct.unpack_from('>II', data, pos); pos += 8
            require(offset < len(strings) and pos+size <= end, 'Invalid FDT property range')
            nul = strings.find(b'\0', offset)
            require(nul >= offset and stack, 'Invalid FDT property name')
            key = ('/'+'/'.join(stack[1:]), strings[offset:nul].decode())
            require(key not in props, 'Duplicate FDT property')
            props[key] = data[pos:pos+size]
            pos = (pos+size+3) & ~3
        elif tag == 4:
            pass
        elif tag == 9:
            require(not stack, 'Unbalanced FDT tree')
            return props
        else:
            raise RuntimeError('Unknown FDT token')
    raise RuntimeError('Missing FDT end token')


def static_arm64(data):
    require(len(data) >= 64 and data[:6] == b'\x7fELF\x02\x01', 'Slot helper must be ELF64 little-endian')
    etype, machine = struct.unpack_from('<HH', data, 16)
    require(etype == 2 and machine == 183, 'Slot helper must be an ARM64 executable')
    phoff = struct.unpack_from('<Q', data, 32)[0]
    phsize, phnum = struct.unpack_from('<HH', data, 54)
    require(phsize == 56 and phnum > 0 and phoff+phnum*phsize <= len(data), 'Invalid helper program headers')
    types = [struct.unpack_from('<I', data, phoff+i*phsize)[0] for i in range(phnum)]
    require(1 in types and 2 not in types and 3 not in types,
            'Slot helper must be statically linked without an interpreter or dynamic segment')
    for forbidden in (b'/opt/sheng-selector', b'slot-switch-init.py'):
        require(forbidden not in data, 'Slot helper refers to the removed updater')


def source_components(args,work):
    capture=json.loads((args.capture/'capture.json').read_text())
    require(capture.get('schema')==1 and capture.get('device')=='sheng' and capture.get('boot_lun')==1,
            "Need this tablet native slot-A capture")
    image=(args.capture/'boot_a.img').read_bytes()
    require(sha(image)==capture['firmware_hashes']['boot_a'] and len(image)==capture['partitions']['boot_a']['bytes'],
            'Captured Android A backup changed')
    b=(args.capture/'boot_b.img').read_bytes()
    require(sha(b)==capture['firmware_hashes']['boot_b'],'Captured recovery B backup changed')
    compressed=(args.linux/'Image.gz').read_bytes()
    kernel,extent,_=arm64_image(compressed)
    require(b'Linux version ' in kernel,'Expected a native Linux Image')
    if not compressed.startswith(b'\x1f\x8b'):compressed=gzip.compress(kernel,mtime=0)
    initrd=(args.linux/'initramfs.gz').read_bytes();entries=read_cpio(initrd)
    require(any(e.name in ('init','./init') for e in entries),'Native initramfs needs /init')
    original_dtb=validate_dtb((args.linux/'linux.dtb').read_bytes())
    cmdline=(args.linux/'cmdline.txt').read_text().strip()
    dtc=ROOT/'.build/u-boot/scripts/dtc/dtc'
    control=prepare_control(original_dtb,cmdline,work,dtc)
    build_manifest=json.loads((ROOT/'.build/u-boot/build-manifest.json').read_text())
    require(sha(args.u_boot.read_bytes())==build_manifest['u_boot_sha256'],'U-Boot build differs from its manifest')
    if build_manifest['slot_switch']:
        names={e.name:e for e in entries}
        require('slot-helper' in names and b'SHENG-SELF-CONTAINED-SLOT-BEGIN' in names['init'].data,
                'Advanced slot build requires a separately qualified helper initramfs')
        static_arm64(names['slot-helper'].data)
    import re
    release=re.search(rb'Linux version ([^\s\0]+)',kernel)
    current={'kernel_release':release.group(1).decode() if release else 'unknown',
             'linux_dtb_sha256':sha(original_dtb),'slot_switch':build_manifest['slot_switch'],
             'cmdline':cmdline,'capture':capture}
    return compressed,initrd,control,current,image


def build(args):
    output = args.output.resolve()
    require(not output.exists(), 'Output directory must not already exist')
    output.mkdir(parents=True)
    work = output/'build'; work.mkdir()
    uboot = args.u_boot.read_bytes()
    require(64 <= len(uboot) < 0x300000 and struct.unpack_from('<Q', uboot, 8)[0] == 0 and
            struct.unpack_from('<Q', uboot, 16)[0] < 0x300000, 'Unsupported U-Boot image layout')
    compressed_kernel, old_initrd, control, current, base_image = source_components(args,work)
    initrd=old_initrd
    differences={"initramfs_unchanged":True}
    stock=android_boot(base_image)
    require(not stock.ramdisk,'This wrapper supports modern Android with companion init_boot/vendor_boot, not a boot ramdisk')
    boot_bytes=current['capture']['partitions']['boot_a']['bytes']
    kernel = gzip.decompress(compressed_kernel)
    require(struct.unpack_from('<Q', kernel, 16)[0] <= stock.extent and len(kernel) <= stock.extent,
            'Linux decompression overlaps wrapper payloads')
    for name, data in [('native-Image.gz',compressed_kernel),('initramfs.gz',initrd),
                       ('source-initramfs.gz',old_initrd),('control.dtb',control),
                       ('u-boot.bin',uboot),('android-sha256.bin',bytes.fromhex(sha(stock.kernel)))]:
        dump(work/name, data)
    its = '''/dts-v1/;
/ { description="Sheng native Android / Linux menu"; #address-cells=<2>;
 images {
  kernel { data=/incbin/("native-Image.gz"); type="kernel"; arch="arm64";
   os="linux"; compression="gzip"; load=<0 0xa8000000>; entry=<0 0xa8000000>; hash { algo="sha256"; }; };
  ramdisk { data=/incbin/("initramfs.gz"); type="ramdisk"; arch="arm64";
   os="linux"; compression="none"; load=<0 0xb2000000>; hash { algo="sha256"; }; };
 };
 configurations { default="native"; native { kernel="kernel"; ramdisk="ramdisk"; }; };
};'''
    (work/'native.its').write_text(its)
    hostbuild = ROOT/'.build/u-boot'
    env = dict(os.environ, SOURCE_DATE_EPOCH='0',
               PATH=str(hostbuild/'scripts/dtc')+os.pathsep+os.environ['PATH'])
    run([hostbuild/'tools/mkimage','-f','native.its','native.itb'],cwd=work,env=env)
    fit = (work/'native.itb').read_bytes()
    require(len(fit) < 0x2000000, 'FIT exceeds verified staging capacity')
    props = parse_fdt(fit)
    require(props[('/images/kernel','data')] == compressed_kernel and props[('/images/ramdisk','data')] == initrd,
            'FIT component changed')
    payload = work/'payload.S'
    payload.write_text('\n'.join('.section '+section+',"a"\n.incbin "'+str(work/name)+'"' for section,name in (
        ('.ubootpayload','u-boot.bin'),('.dtbpayload','control.dtb'),
        ('.hashpayload','android-sha256.bin'),('.fitpayload','native.itb')))+'\n')
    menu, lib, source = ROOT/'src/wrapper', ROOT/'vendor/libfdt', ROOT/'src/wrapper'
    flags = ['--target=aarch64-none-elf','-O2','-ffreestanding','-fPIC','-fvisibility=hidden',
        '-fno-stack-protector','-fno-builtin','-fno-strict-aliasing','-fno-unwind-tables',
        '-fno-asynchronous-unwind-tables','-fno-vectorize','-fno-slp-vectorize',
        '-mgeneral-regs-only','-mstrict-align','-mbranch-protection=none',
        '-ffunction-sections','-fdata-sections','-I'+str(source/'include'),'-I'+str(lib),
        f'-DSELECTOR_OFFSET={stock.extent}',f'-DORIGINAL_CODE1={stock.code1}',
        f'-DORIGINAL_ENTRY_OFFSET={stock.entry}',f'-DORIGINAL_BYTES={len(stock.kernel)}']
    sources = [menu/'menu-entry.S',menu/'menu-prepare.c',source/'string.c',payload,
               *[lib/n for n in ('fdt.c','fdt_ro.c','fdt_rw.c','fdt_wip.c','fdt_check.c')]]
    source_hashes = {str(p):sha(p.read_bytes()) for p in sources}
    objects = []
    for path in sources:
        obj=work/(path.stem+'.o'); run(['clang',*flags,'-c',path,'-o',obj]); objects.append(obj)
    elf=work/'selector.elf'
    run(['ld.lld','--gc-sections','--no-undefined','-T',menu/'menu.ld','-o',elf,*objects])
    require(all(sha(Path(p).read_bytes()) == digest for p,digest in source_hashes.items()),
            'Wrapper source changed during build')
    raw=elf.read_bytes(); shoff=struct.unpack_from('<Q',raw,40)[0]
    step,count,idx=struct.unpack_from('<HHH',raw,58)
    headers=[struct.unpack_from('<IIQQQQIIQQ',raw,shoff+i*step) for i in range(count)]
    strings=raw[headers[idx][4]:headers[idx][4]+headers[idx][5]]
    for h in headers:
        name=strings[h[0]:].split(b'\0',1)[0].decode()
        require(not h[5] or (name not in ('.got','.got.plt','.dynamic','.plt','.interp') and h[1] not in (4,9)),
                'Dynamic code or runtime relocation in wrapper')
    symbols={line.split()[2]:int(line.split()[0],16) for line in run(['llvm-nm','-n',elf]).splitlines() if len(line.split())==3}
    require(symbols['selector_entry']==0 and symbols['control_dtb'] >= symbols['uboot_blob']+0x300000,
            'Unsupported wrapper staging layout')
    run(['llvm-objcopy','-O','binary',elf,work/'selector.bin'])
    blob=(work/'selector.bin').read_bytes(); require(len(blob)==symbols['selector_end'], 'Wrapper extent mismatch')
    wrapped=bytearray(stock.kernel)+bytes(stock.extent-len(stock.kernel))+blob
    wrapped+=bytes(-len(wrapped)%0x200000)
    require(0xa8080000+len(wrapped)<=0xb0000000 and len(wrapped)<=0x6000000,
            'Combined payload exceeds verified RAM layout')
    require(stock.extent-4 < 1<<27, 'Wrapper outside ARM64 branch reach')
    struct.pack_into('<I',wrapped,4,0x14000000|((stock.extent-4)//4))
    struct.pack_into('<Q',wrapped,16,len(wrapped))
    require(wrapped[:4] == stock.kernel[:4] and wrapped[24:len(stock.kernel)] == stock.kernel[24:],
            'Original Android body changed')
    header=bytearray(stock.header); struct.pack_into('<I',header,8,len(wrapped))
    if stock.version==4: struct.pack_into('<I',header,1580,0)
    image=bytes(header)+wrapped+bytes(-len(wrapped)%4096)+stock.ramdisk
    image+=bytes(-len(image)%4096); require(len(image)<=boot_bytes,'Boot capacity exceeded')
    dump(output/'selector.img',image)
    # Installation writes only this menu prefix. The known full boot partition
    # contains historical nonzero data beyond it, which must remain untouched.
    fullhash=hashlib.sha256(image)
    fullhash.update(memoryview(base_image)[len(image):])
    suffix_sha=sha(memoryview(base_image)[len(image):])
    report = dict(schema=3,boot_mode='native-menu',uefi_loader=False,
        image_sha256=sha(image),image_bytes=len(image),full_partition_sha256=fullhash.hexdigest(),
        full_partition_tail_policy='preserve-existing-suffix',preserved_suffix_sha256=suffix_sha,
        preserved_suffix_offset=len(image),preserved_suffix_bytes=boot_bytes-len(image),
        android_header_version=stock.version,original_android_boot_sha256=sha(stock.original),
        original_android_kernel_sha256=sha(stock.kernel),original_kernel_bytes=len(stock.kernel),
        original_image_size=stock.extent,original_code1=stock.code1,original_entry_offset=stock.entry,
        selector_offset=stock.extent,wrapped_kernel_bytes=len(wrapped),android_ramdisk_sha256=sha(stock.ramdisk),
        android_kernel_was_gzip=stock.compressed,symbols=symbols,linux_kernel_sha256=sha(kernel),
        linux_dtb_sha256=current['linux_dtb_sha256'],kernel_release=current['kernel_release'],
        initramfs_sha256=sha(initrd),fit_sha256=sha(fit),u_boot_sha256=sha(uboot),
        control_dtb_sha256=sha(control),force_linux_control=False,hardware_tested=False,
        requires_matching_android_companions=True,requires_installed_updater=False,
        source_capture_sha256=sha((args.capture/'capture.json').read_bytes()),
        slot_switch_enabled=current['slot_switch'],initramfs_unchanged=True,
        wrapper_qa_passed=False)
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    (output/'differences.json').write_text(json.dumps(differences,indent=2)+'\n')
    (output/'selector.img.sha256').write_text(report['image_sha256']+'  selector.img\n')
    print(json.dumps({k:report[k] for k in ('image_sha256','image_bytes','full_partition_sha256','kernel_release','slot_switch_enabled')},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True,help='Private capture directory from this tablet')
    parser.add_argument('--linux',type=Path,required=True,help='Private Image.gz, linux.dtb, initramfs.gz and cmdline.txt')
    parser.add_argument('--u-boot',type=Path,default=ROOT/'.build/u-boot/u-boot-nodtb.bin')
    parser.add_argument('--output',type=Path,default=ROOT/'.build/menu')
    args=parser.parse_args();args.capture=args.capture.resolve();args.linux=args.linux.resolve()
    build(args)
