#!/usr/bin/env python3
"""Mock-file transaction tests; never uses USB, SSH, mounts or device writes."""
import copy
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import uuid
import zlib

HERE=Path(__file__).resolve().parent
from synthetic_fixture import baseline,FS_UUID
spec=importlib.util.spec_from_file_location('profile_generator',HERE/'generate_profile.py')
generator=importlib.util.module_from_spec(spec);spec.loader.exec_module(generator)
ORIGINAL=baseline()
CORE=generator.CORE_HASH_NAMES

ACTIVE=1<<50;SUCCESS=1<<54


def recalc(h,t):
    h=bytearray(h);struct.pack_into('<I',h,88,zlib.crc32(t));h[16:20]=bytes(4)
    struct.pack_into('<I',h,16,zlib.crc32(h[:92]));return bytes(h)


class Fixture:
    def __init__(self, directory):
        self.dir=Path(directory);self.baseline=copy.deepcopy(ORIGINAL);self.disks={};self.parts={};self.hashes={}
        self.size=8*1024*1024;last=self.size//4096-1
        (self.dir/'.slot-mock-fixture').write_text('SHENG-SLOT-MOCK-V2\n')
        for guid,d in self.baseline['gpt'].items():
            table=bytearray.fromhex(d['copies'][0]['table'])
            for off in range(0,len(table),128):
                e=table[off:off+128]
                if e[:16]==bytes(16):continue
                name=e[56:].decode('utf-16le').split('\0',1)[0]
                lo=32+(off//128)*16;hi=lo+15
                struct.pack_into('<QQ',table,off+32,lo,hi)
                self.parts[name]=(guid,off,lo,hi)
            headers=[]
            for idx,old in enumerate(d['copies']):
                h=bytearray.fromhex(old['header']);struct.pack_into('<QQQQ',h,24,last if idx else 1,1 if idx else last,6,last-5)
                struct.pack_into('<Q',h,72,last-4 if idx else 2)
                headers.append(recalc(h,table))
            for i in range(2):d['copies'][i]['header']=headers[i].hex();d['copies'][i]['table']=table.hex()
            path=self.dir/('disk-'+uuid.UUID(guid).bytes_le.hex()+'.img');self.disks[guid]=path
            with open(path,'wb') as f:f.truncate(self.size)
            with open(path,'r+b',buffering=0) as f:
                f.seek(4096);f.write(headers[0]);f.seek(8192);f.write(table)
                f.seek((last-4)*4096);f.write(table);f.seek(last*4096);f.write(headers[1])
        for name,(guid,off,lo,hi) in self.parts.items():
            data=bytearray((hi-lo+1)*4096)
            marker=hashlib.sha256(name.encode()).digest();data[:32]=marker;data[-32:]=marker
            if name in ('boot_a','boot_b'):data[:8]=b'ANDROID!'
            if name=='linux':
                data[:]=bytes(len(data));sb=memoryview(data)[1024:2048]
                struct.pack_into('<I',sb,4,16);struct.pack_into('<I',sb,24,2)
                struct.pack_into('<HH',sb,56,0xef53,1);sb[104:120]=uuid.UUID(FS_UUID).bytes
            if name=='misc':
                data[:]=bytes(len(data));struct.pack_into('<BIBB',data,32768,2,0x56740ab0,0,0)
            with open(self.disks[guid],'r+b',buffering=0) as f:f.seek(lo*4096);f.write(data)
            self.hashes[name]=hashlib.sha256(data).hexdigest()
        fw={n:self.hashes[n] for n in CORE}
        raw,report=generator.make_profile(self.baseline,fw,fw['boot_a'],fw['boot_b'],FS_UUID)
        self.profile=self.dir/'profile.bin';self.profile.write_bytes(raw)
        (self.dir/'boot_lun.bin').write_bytes(struct.pack('<I',1))
        nvmem=bytearray((i*29+7)&255 for i in range(256));nvmem[0x48]=5
        (self.dir/'nvmem.bin').write_bytes(nvmem)

    def meta(self):
        out={}
        for guid,path in self.disks.items():
            with open(path,'rb',buffering=0) as f:
                f.seek(4096);ph=f.read(4096);count,step=struct.unpack_from('<II',ph,80)
                f.seek(8192);pt=f.read(count*step)
                f.seek(self.size-4096);bh=f.read(4096)
                f.seek(struct.unpack_from('<Q',bh,72)[0]*4096);bt=f.read(count*step)
            out[guid]=(ph,pt,bh,bt)
        return out

    def all_partition_hashes(self):
        result={}
        for name,(guid,off,lo,hi) in self.parts.items():
            with open(self.disks[guid],'rb',buffering=0) as f:f.seek(lo*4096);data=f.read((hi-lo+1)*4096)
            result[name]=hashlib.sha256(data).hexdigest()
        return result

    def mutate_entry(self,name,fn):
        guid,off,_,_=self.parts[name];path=self.disks[guid];ph,pt,bh,bt=self.meta()[guid]
        arrays=[]
        for t in (pt,bt):
            t=bytearray(t);e=bytearray(t[off:off+128]);fn(e);t[off:off+128]=e;arrays.append(bytes(t))
        with open(path,'r+b',buffering=0) as f:
            f.seek(4096);f.write(recalc(ph,arrays[0]));f.seek(8192);f.write(arrays[0])
            f.seek(struct.unpack_from('<Q',bh,72)[0]*4096);f.write(arrays[1]);f.seek(self.size-4096);f.write(recalc(bh,arrays[1]))


class SlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bin=HERE/'bin';cls.bin.mkdir(exist_ok=True)
        common=['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror']
        for name,flags in [('slot-helper-host',[]),('slot-helper-test',['-DSLOT_TEST_BUILD'])]:
            subprocess.run(common+flags+[str(HERE/'slot_helper.c'),str(HERE/'hash.c'),'-o',str(cls.bin/name)],check=True)
        subprocess.run(['cc','-shared','-fPIC','-O2',str(HERE/'hash.c'),'-o',str(cls.bin/'hash-test.so')],check=True)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sheng-slot-fixture-');self.fixture=Fixture(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def run_helper(self,*args,production=False):
        exe=self.bin/('slot-helper-host' if production else 'slot-helper-test')
        return subprocess.run([str(exe),'--mock',str(self.fixture.dir),'--profile',str(self.fixture.profile),*args],capture_output=True,text=True,timeout=20)

    def test_dry_run_production_leaves_everything_unchanged(self):
        f=self.fixture;before=f.meta();images=f.all_partition_hashes();lun=(f.dir/'boot_lun.bin').read_bytes();nvmem=(f.dir/'nvmem.bin').read_bytes()
        result=self.run_helper(production=True)
        self.assertEqual(result.returncode,10,result.stderr);self.assertIn('dry-run-qualified',result.stdout)
        self.assertEqual(f.meta(),before);self.assertEqual(f.all_partition_hashes(),images)
        self.assertEqual((f.dir/'boot_lun.bin').read_bytes(),lun);self.assertEqual((f.dir/'nvmem.bin').read_bytes(),nvmem)
        self.assertFalse((f.dir/'transactions').exists())

    def test_a_b_a_roundtrip_preserves_all_partition_contents_and_health(self):
        f=self.fixture;before=f.meta();images=f.all_partition_hashes();original_nvmem=(f.dir/'nvmem.bin').read_bytes()
        p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual((f.dir/'boot_lun.bin').read_bytes(),struct.pack('<I',2))
        after=f.meta()
        for guid,(ph,pt,bh,bt) in after.items():
            self.assertEqual(pt,bt);self.assertEqual(zlib.crc32(pt),struct.unpack_from('<I',ph,88)[0])
            self.assertEqual(zlib.crc32(bt),struct.unpack_from('<I',bh,88)[0])
            for off in range(0,len(pt),128):
                if pt[off:off+16]==bytes(16):continue
                name=pt[off+56:off+128].decode('utf-16le').split('\0',1)[0]
                old=before[guid][1][off:off+128];new=pt[off:off+128]
                self.assertEqual(old[16:48]+old[56:],new[16:48]+new[56:])
                oa,na=struct.unpack_from('<Q',old,48)[0],struct.unpack_from('<Q',new,48)[0]
                self.assertEqual((oa^na)&~(ACTIVE|(3<<48)),0)
                self.assertEqual(bool(na&ACTIVE),name.endswith('_b'))
        p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(f.meta(),before);self.assertEqual(f.all_partition_hashes(),images)
        self.assertEqual((f.dir/'boot_lun.bin').read_bytes(),struct.pack('<I',1))
        changed_nvmem=bytearray(original_nvmem);changed_nvmem[0x48]&=1
        self.assertEqual((f.dir/'nvmem.bin').read_bytes(),changed_nvmem)
        self.assertEqual((f.dir/'nvmem.bin').read_bytes()[0xbc:0xc0],original_nvmem[0xbc:0xc0])
        self.assertEqual(len(list((f.dir/'transactions').glob('slot-*'))),2)

    def test_every_partial_write_rolls_back_or_stops(self):
        # Four changed GPT disks * four writes + UFS attr + PMIC byte =18.
        f=self.fixture;original=f.meta();images=f.all_partition_hashes();lun=(f.dir/'boot_lun.bin').read_bytes();nv=(f.dir/'nvmem.bin').read_bytes()
        for number in range(1,19):
            with self.subTest(write=number):
                p=self.run_helper('--apply-mock','--fault-at',str(number))
                self.assertEqual(p.returncode,20,p.stderr)
                self.assertIn('complete source GPT/UFS/PMIC state restored',p.stderr)
                self.assertEqual(f.meta(),original);self.assertEqual(f.all_partition_hashes(),images)
                self.assertEqual((f.dir/'boot_lun.bin').read_bytes(),lun);self.assertEqual((f.dir/'nvmem.bin').read_bytes(),nv)

    def test_persistent_failure_never_reports_safe_rollback(self):
        p=self.run_helper('--apply-mock','--fault-at','1','--persistent-fault')
        self.assertEqual(p.returncode,30,p.stderr);self.assertIn('rollback uncertain',p.stderr)

    def test_dirty_root_refuses_before_transaction(self):
        f=self.fixture;guid,_,lo,_=f.parts['linux']
        with open(f.disks[guid],'r+b',buffering=0) as o:o.seek(lo*4096+1024+96);o.write(struct.pack('<I',4))
        before=f.meta();p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,10,p.stderr)
        self.assertIn('root ext4 is dirty',p.stderr);self.assertEqual(f.meta(),before);self.assertFalse((f.dir/'transactions').exists())

    def test_unhealthy_target_refuses(self):
        self.fixture.mutate_entry('boot_b',lambda e:struct.pack_into('<Q',e,48,struct.unpack_from('<Q',e,48)[0]&~SUCCESS))
        before=self.fixture.meta();p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,10,p.stderr)
        self.assertIn('successful and bootable',p.stderr);self.assertEqual(self.fixture.meta(),before)

    def test_unsuffixed_active_refuses(self):
        self.fixture.mutate_entry('linux',lambda e:struct.pack_into('<Q',e,48,struct.unpack_from('<Q',e,48)[0]|ACTIVE))
        before=self.fixture.meta();p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,10,p.stderr)
        self.assertIn('incoherent ACTIVE',p.stderr);self.assertEqual(self.fixture.meta(),before)

    def test_incoherent_guid_refuses(self):
        def bad(e):e[:16]=bytes.fromhex('77036cd403d542bb8ed137e5a88baa34')
        self.fixture.mutate_entry('boot_a',bad)
        p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,10,p.stderr);self.assertIn('layout/type GUID changed',p.stderr)

    def test_corrupted_profile_refuses(self):
        f=self.fixture;raw=bytearray(f.profile.read_bytes());raw[-1]^=1;f.profile.write_bytes(raw)
        p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,10,p.stderr);self.assertIn('profile payload checksum',p.stderr)

    def test_corrupted_backup_crc_refuses(self):
        f=self.fixture;path=next(iter(f.disks.values()))
        with open(path,'r+b',buffering=0) as o:o.seek(f.size-4096+16);o.write(bytes(4))
        before=f.meta();p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,10,p.stderr)
        self.assertIn('GPT header identity/CRC',p.stderr);self.assertEqual(f.meta(),before)

    def test_changed_firmware_refuses(self):
        f=self.fixture;guid,_,lo,_=f.parts['tz_b']
        with open(f.disks[guid],'r+b',buffering=0) as o:o.seek(lo*4096+100);o.write(b'changed')
        before=f.meta();p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,10,p.stderr)
        self.assertIn('firmware/image hash differs: tz_b',p.stderr);self.assertEqual(f.meta(),before)

    def test_ota_merge_refuses(self):
        f=self.fixture;guid,_,lo,_=f.parts['misc']
        with open(f.disks[guid],'r+b',buffering=0) as o:o.seek(lo*4096+32768+5);o.write(bytes([3]))
        p=self.run_helper('--apply-mock');self.assertEqual(p.returncode,10,p.stderr);self.assertIn('OTA snapshot operation',p.stderr)

    def test_mock_symlink_nvmem_refuses(self):
        p=self.fixture.dir/'nvmem.bin';p.unlink();p.symlink_to('/dev/null')
        result=self.run_helper('--apply-mock');self.assertEqual(result.returncode,10,result.stderr)
        self.assertFalse((self.fixture.dir/'transactions').exists())

    def test_mock_hardlink_ufs_refuses(self):
        p=self.fixture.dir/'boot_lun.bin';os.link(p,self.fixture.dir/'lun-alias')
        result=self.run_helper('--apply-mock');self.assertEqual(result.returncode,10,result.stderr)
        self.assertIn('not a regular four-byte file',result.stderr)

    def test_mock_nonregular_files_refuse_without_waiting(self):
        for name in ('.slot-mock-fixture', 'boot_lun.bin', 'nvmem.bin'):
            with self.subTest(name=name):
                f=self.fixture;p=f.dir/name;old=p.read_bytes();p.unlink();os.mkfifo(p)
                result=self.run_helper('--apply-mock');self.assertEqual(result.returncode,10,result.stderr)
                self.assertFalse((f.dir/'transactions').exists())
                p.unlink();p.write_bytes(old)

    def test_production_has_no_mock_apply_or_fault_switch(self):
        for arg in ('--apply-mock','--fault-at','--test-transform'):
            p=self.run_helper(arg,production=True);self.assertEqual(p.returncode,10,p.stderr)
            self.assertIn('Usage:',p.stderr)

    def test_generator_rejects_overlapping_layout(self):
        f=self.fixture;x=copy.deepcopy(f.baseline);first=next(iter(x['gpt'].values()))
        t=bytearray.fromhex(first['copies'][0]['table']);a=struct.unpack_from('<QQ',t,32)
        struct.pack_into('<QQ',t,128+32,*a)
        for c in first['copies']:c['table']=t.hex();c['header']=recalc(bytes.fromhex(c['header']),t).hex()
        with self.assertRaisesRegex(ValueError,'overlap'):
            generator.make_profile(x,{n:f.hashes[n] for n in CORE},f.hashes['boot_a'],f.hashes['boot_b'],FS_UUID)

    def test_hash_functions_match_standard_libraries(self):
        lib=ctypes.CDLL(str(self.bin/'hash-test.so'))
        lib.slot_sha.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p]
        lib.slot_crc.argtypes=[ctypes.c_void_p,ctypes.c_size_t];lib.slot_crc.restype=ctypes.c_uint32
        for size in (0,1,55,56,63,64,65,1000,1024*1024+37):
            data=bytes((i*43+11)&255 for i in range(size));buf=ctypes.create_string_buffer(data);out=ctypes.create_string_buffer(32)
            lib.slot_sha(buf,size,out)
            self.assertEqual(out.raw,hashlib.sha256(data).digest());self.assertEqual(lib.slot_crc(buf,size),zlib.crc32(data))


if __name__=='__main__':unittest.main(verbosity=2)
