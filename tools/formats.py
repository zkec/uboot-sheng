# SPDX-License-Identifier: GPL-3.0-only
"""Strict parsers for the supported Android, ARM64 and initramfs formats."""
from dataclasses import dataclass
import gzip
import io
from pathlib import PurePosixPath
import struct

from common import Error, require, sha

MAX_KERNEL = 0x6000000

def align(value, size):
    return (value + size - 1) // size * size

def gunzip(data, limit=MAX_KERNEL):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        out = stream.read(limit + 1)
    require(len(out) <= limit, 'Compressed input exceeds the supported size.')
    return out

def arm64_image(data, efi=False):
    compressed = data.startswith(b'\x1f\x8b')
    raw = gunzip(data) if compressed else data
    require(64 <= len(raw) <= MAX_KERNEL, 'Unsupported kernel size.')
    require(raw[56:60] == b'ARM\x64', 'Expected a raw/gzip ARM64 Image, not a boot image or Image.gz-dtb.')
    size = struct.unpack_from('<Q', raw, 16)[0]
    flags = struct.unpack_from('<Q', raw, 24)[0]
    require(64 <= size <= MAX_KERNEL and not flags & 1, 'Unsupported ARM64 image extent or endianness.')
    if efi:
        require(raw[:2] == b'MZ', 'The Linux kernel must include its EFI stub (CONFIG_EFI_STUB).')
        pe = struct.unpack_from('<I', raw, 60)[0]
        require(pe + 96 <= len(raw) and raw[pe:pe+4] == b'PE\0\0', 'Invalid Linux PE/COFF header.')
        require(struct.unpack_from('<H', raw, pe+4)[0] == 0xaa64, 'Linux EFI machine must be ARM64.')
        require(struct.unpack_from('<H', raw, pe+24+68)[0] == 10, 'Linux EFI subsystem must be EFI application.')
    return raw, size, compressed

@dataclass
class AndroidBoot:
    original: bytes
    header: bytes
    kernel: bytes
    ramdisk: bytes
    version: int
    extent: int
    code1: int
    entry: int
    compressed: bool

def android_boot(data):
    require(len(data) >= 4096 and data[:8] == b'ANDROID!', 'Expected a stock Android boot.img.')
    version = struct.unpack_from('<I', data, 40)[0]
    require(version in (3, 4), 'Only Android boot header versions 3 and 4 are supported.')
    ksize, rsize = struct.unpack_from('<II', data, 8)
    hsize = struct.unpack_from('<I', data, 20)[0]
    require(hsize == (1580 if version == 3 else 1584), 'Unexpected Android header size.')
    require(64 <= ksize <= MAX_KERNEL and rsize <= 0x2000000, 'Android kernel/ramdisk exceeds limits.')
    roff = 4096 + align(ksize, 4096)
    require(4096 + ksize <= len(data) and roff + rsize <= len(data), 'Truncated Android boot image.')
    kernel, extent, compressed = arm64_image(data[4096:4096+ksize])
    require(b'SHENG-EARLY-HALL' not in kernel and b'SHENG-UBOOT-MENU' not in kernel, 'This is already a selector image. Supply the unwrapped Android boot.img.')
    require(len(kernel) <= extent, 'Android file extends past image_size; this layout needs explicit support.')
    code1 = struct.unpack_from('<I', kernel, 4)[0]
    require(code1 >> 26 == 5, 'Android header code1 is not the supported ARM64 B instruction.')
    imm = code1 & 0x3ffffff
    if imm & (1 << 25):
        imm -= 1 << 26
    entry = 4 + imm * 4
    require(64 <= entry < len(kernel), 'Android branch target is outside the original kernel.')
    require(extent % 4096 == 0 and extent < 0x8000000, 'Unsupported Android extent/alignment.')
    return AndroidBoot(data, data[:4096], kernel, data[roff:roff+rsize], version,
                       extent, code1, entry, compressed)

def validate_dtb(data):
    require(40 <= len(data) <= 0x200000, 'Invalid DTB size.')
    h = struct.unpack_from('>10I', data)
    require(h[0] == 0xd00dfeed and 40 <= h[1] <= len(data), 'Invalid DTB header.')
    total=h[1];pos=h[2];end=pos+h[9];strings=data[h[3]:h[3]+h[8]]
    require(40<=pos<end<=total and h[3]+h[8]<=total,'Invalid DTB block ranges.')
    depth=0;compatible=None;finished=False
    while pos+4<=end:
        tag=struct.unpack_from('>I',data,pos)[0];pos+=4
        if tag==1:
            nul=data.find(b'\0',pos,end)
            require(nul>=pos,'Unterminated DTB node.')
            pos=align(nul+1,4);depth+=1
        elif tag==2:
            depth-=1;require(depth>=0,'Unbalanced DTB nodes.')
        elif tag==3:
            require(pos+8<=end,'Truncated DTB property.')
            size,nameoff=struct.unpack_from('>II',data,pos);pos+=8
            require(nameoff<len(strings) and pos+size<=end,'Invalid DTB property bounds.')
            nul=strings.find(b'\0',nameoff)
            require(nul>=nameoff,'Unterminated DTB property name.')
            if depth==1 and strings[nameoff:nul]==b'compatible':
                compatible=data[pos:pos+size].split(b'\0')
            pos=align(pos+size,4)
        elif tag==4:
            pass
        elif tag==9:
            require(depth==0,'Unbalanced DTB tree.')
            finished=True;break
        else:
            raise Error('Unknown DTB structure tag.')
    require(finished and compatible and b'xiaomi,sheng' in compatible,'Device tree root does not identify xiaomi,sheng.')
    return data[:h[1]]

@dataclass
class CpioEntry:
    name: str
    fields: list
    data: bytes

def read_cpio(data):
    raw = gunzip(data, 0x8000000) if data.startswith(b'\x1f\x8b') else data
    entries, names, pos = [], set(), 0
    while pos + 110 <= len(raw):
        require(raw[pos:pos+6] == b'070701', 'Only uncompressed/gzip newc initramfs is supported.')
        fields = [int(raw[pos+6+i*8:pos+14+i*8], 16) for i in range(13)]
        n = fields[11]
        require(1 <= n <= 4096 and pos+110+n <= len(raw), 'Invalid cpio filename length.')
        encoded = raw[pos+110:pos+110+n]
        require(encoded[-1:] == b'\0', 'Unterminated cpio name.')
        name = encoded[:-1].decode()
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts and name not in names, 'Unsafe/duplicate cpio name.')
        names.add(name)
        pos = align(pos+110+n, 4)
        size = fields[6]
        require(size <= 0x8000000 and pos+size <= len(raw), 'Truncated cpio entry.')
        entries.append(CpioEntry(name, fields, raw[pos:pos+size]))
        pos = align(pos+size, 4)
        if name == 'TRAILER!!!':
            return entries
    raise Error('Missing cpio trailer.')

def write_cpio(entries):
    raw = bytearray()
    for inode, entry in enumerate(entries, 1):
        name = entry.name.encode() + b'\0'
        fields = list(entry.fields)
        fields[0], fields[6], fields[11] = inode, len(entry.data), len(name)
        raw += b'070701' + b''.join(f'{n:08x}'.encode() for n in fields) + name
        raw += bytes((-len(raw)) % 4)
        raw += entry.data
        raw += bytes((-len(raw)) % 4)
    raw += bytes((-len(raw)) % 512)
    return gzip.compress(raw, mtime=0)
