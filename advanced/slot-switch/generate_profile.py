#!/usr/bin/env python3
"""Generate bounded, checksummed data profile for the temporary native helper.

No device writes; hashes describe full STORED partition contents. A candidate
image prefix's SHA is not the full boot partition SHA. The caller must supply
the full boot A digest after choosing its padding/write policy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import uuid
import zlib

MAGIC = b'SHENG-SLOT-V2'.ljust(16, b'\0')
MAX_BYTES = 128 * 1024
HEADER = struct.Struct('<16sIIII16s32s')
DISK = struct.Struct('<16sQIIII')
HASH = struct.Struct('<36sQ32s')
CORE_HASH_NAMES = {base+'_'+slot for base in
    'abl aop boot devcfg dtbo hyp init_boot keymaster modem qupfw tz vbmeta vbmeta_system vendor_boot xbl xbl_config xbl_ramdump'.split()
    for slot in ('a','b')}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def checked_header(raw, expected_lba):
    require(len(raw) == 4096 and raw[:8] == b'EFI PART', 'Invalid GPT header')
    require(struct.unpack_from('<I', raw, 8)[0] == 0x10000, 'Unknown GPT revision')
    require(struct.unpack_from('<I', raw, 12)[0] == 92, 'Unexpected GPT header size')
    require(struct.unpack_from('<Q', raw, 24)[0] == expected_lba, 'Wrong GPT location')
    check = bytearray(raw[:92]); check[16:20] = bytes(4)
    require(zlib.crc32(check) == struct.unpack_from('<I', raw, 16)[0], 'Bad header CRC')


def make_profile(before, firmware, boot_a_sha, boot_b_sha, filesystem_uuid, extra_hashes=None):
    gpt = before['gpt'] if 'gpt' in before else before
    require(len(gpt) == 6, 'Exactly six baseline disks are required')
    hashes = dict(firmware.get('protected_hashes', firmware))
    hashes.pop('misc', None)
    if extra_hashes:
        extra = extra_hashes.get('protected_hashes', extra_hashes)
        for name, digest in extra.items():
            require(name not in hashes or hashes[name] == digest, 'Conflicting extra firmware hash: '+name)
            hashes[name] = digest
    hashes['boot_a'] = boot_a_sha
    hashes['boot_b'] = boot_b_sha
    require(CORE_HASH_NAMES <= hashes.keys() and 34 <= len(hashes) <= 128,
            'Expected 34 mandatory firmware hashes and at most 128 total')
    payload = bytearray(); parts = {}; source_a_tables = []
    report_disks = []
    for guid, disk in sorted(gpt.items()):
        require(len(disk['copies']) == 2, 'Two baseline GPT copies are required')
        ph, bh = [bytes.fromhex(c['header']) for c in disk['copies']]
        pt, bt = [bytes.fromhex(c['table']) for c in disk['copies']]
        last = struct.unpack_from('<Q', ph, 32)[0]
        checked_header(ph, 1); checked_header(bh, last)
        require(ph[56:72] == bh[56:72] == uuid.UUID(guid).bytes_le, 'Disk UUID mismatch')
        require(struct.unpack_from('<Q', bh, 32)[0] == 1, 'Wrong backup alternate LBA')
        count, step = struct.unpack_from('<II', ph, 80)
        require(0 < count <= 128 and step == 128 and len(pt) == count * step,
                'Unsupported GPT entry array')
        require(ph[40:56] == bh[40:56] and ph[80:88] == bh[80:88], 'GPT header geometry differs')
        first_usable, last_usable = struct.unpack_from('<QQ', ph, 40)
        require(2 < first_usable <= last_usable < last, 'Invalid usable GPT bounds')
        table_extents = []
        for h in (ph, bh):
            start = struct.unpack_from('<Q', h, 72)[0]
            require(2 <= start < last and start*4096+len(pt) <= last*4096, 'Table outside device')
            table_extents.append((start*4096, start*4096+len(pt)))
        require(table_extents[0][1] <= table_extents[1][0], 'GPT arrays overlap')
        disk_ranges = []
        require(zlib.crc32(pt) == struct.unpack_from('<I', ph, 88)[0], 'Primary table CRC')
        require(zlib.crc32(bt) == struct.unpack_from('<I', bh, 88)[0], 'Backup table CRC')
        require(len(bt) == len(pt), 'GPT lengths differ')
        for off in range(0, len(pt), 128):
            a, b = pt[off:off+128], bt[off:off+128]
            require(a[:48] == b[:48] and a[56:] == b[56:], 'Baseline copies differ in layout')
            label = a[56:].decode('utf-16le').split('\0', 1)[0]
            delta = int.from_bytes(a[48:56], 'little') ^ int.from_bytes(b[48:56], 'little')
            require(delta == 0 or label in ('boot_a', 'boot_b') and delta & ~(0x38 << 48) == 0,
                    'Baseline copies differ beyond boot retry bits')
            if a[:16] == bytes(16):
                continue
            require(re.fullmatch(r'[A-Za-z0-9_.-]{1,35}', label), 'Unsupported partition label')
            require(label not in parts, 'Duplicate partition label')
            lo, hi = struct.unpack_from('<QQ', a, 32)
            require(first_usable <= lo <= hi <= last_usable, 'Partition outside usable disk')
            require(all(hi < a or lo > b for a,b in disk_ranges), 'Partitions overlap')
            require(all(end <= lo*4096 or begin >= (hi+1)*4096 for begin,end in table_extents),
                    'Partition overlaps GPT array')
            disk_ranges.append((lo,hi))
            parts[label] = {'bytes': (hi-lo+1)*4096, 'entry': a, 'disk': guid}
        # Pin canonical primary entries in both templates, so templates always
        # agree even if firmware updated only one copy's retry count.
        canonical_backup = bytearray(bh)
        struct.pack_into('<I', canonical_backup, 88, zlib.crc32(pt))
        canonical_backup[16:20] = bytes(4)
        struct.pack_into('<I', canonical_backup, 16, zlib.crc32(canonical_backup[:92]))
        payload += DISK.pack(ph[56:72], (last+1)*4096, 4096, count, step, 0)
        payload += ph + canonical_backup + pt
        source_a_tables.append(pt)
        report_disks.append({'uuid':guid, 'bytes':(last+1)*4096, 'entries':count,
                             'primary_entries_lba':struct.unpack_from('<Q',ph,72)[0],
                             'backup_entries_lba':struct.unpack_from('<Q',bh,72)[0]})
    for name, item in parts.items():
        entry = item['entry']; attrs = int.from_bytes(entry[48:56], 'little')
        suffix = name[-2:] if name.endswith(('_a', '_b')) else None
        if suffix:
            other = name[:-1] + ('b' if suffix == '_a' else 'a')
            require(other in parts, 'Missing paired partition: '+other)
            require(bool(attrs & (1 << 50)) == (suffix == '_a'), 'Baseline is not coherent source A')
        else:
            require(not attrs & (1 << 50), 'Unexpected unsuffixed ACTIVE partition')
    for name in ('boot_a', 'boot_b'):
        attrs = int.from_bytes(parts[name]['entry'][48:56], 'little')
        require(attrs & (1 << 54) and not attrs & (1 << 55), 'Both boot slots must be successful')
    records = []
    for name, digest in sorted(hashes.items()):
        require(name in parts and re.fullmatch('[0-9a-fA-F]{64}', digest), 'Invalid firmware hash: '+name)
        encoded = name.encode('ascii')
        payload += HASH.pack(encoded.ljust(36,b'\0'), parts[name]['bytes'], bytes.fromhex(digest))
        records.append({'name':name,'bytes':parts[name]['bytes'],'sha256':digest.lower()})
    total = HEADER.size + len(payload)
    require(total <= MAX_BYTES, 'Profile exceeds the bounded format')
    raw = HEADER.pack(MAGIC,2,total,6,len(hashes),uuid.UUID(filesystem_uuid).bytes,
                      hashlib.sha256(payload).digest()) + payload
    return raw, {'format':2,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                 'payload_sha256':hashlib.sha256(payload).hexdigest(),
                 'filesystem_uuid':filesystem_uuid,'disks':report_disks,'firmware':records}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--firmware-hashes', type=Path, required=True)
    p.add_argument('--extra-hashes', type=Path, help='optional additional static paired image hashes')
    p.add_argument('--boot-a-sha256', required=True, help='full stored boot_a partition digest')
    p.add_argument('--boot-b-sha256', required=True, help='full stored boot_b partition digest')
    p.add_argument('--filesystem-uuid', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    raw, report = make_profile(json.loads(args.baseline.read_text()),
        json.loads(args.firmware_hashes.read_text()), args.boot_a_sha256,
        args.boot_b_sha256, args.filesystem_uuid,
        json.loads(args.extra_hashes.read_text()) if args.extra_hashes else None)
    args.output.write_bytes(raw)
    args.output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'profile':str(args.output),'bytes':len(raw),'sha256':report['sha256']}))


if __name__ == '__main__': main()
