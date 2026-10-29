# SPDX-License-Identifier: GPL-3.0-only
"""Host-only tests. All write targets are disposable regular files."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
sys.path.insert(0,str(ROOT/'advanced/slot-switch'))
from common import Error,digest
from formats import android_boot,validate_dtb
from fdt import properties
from device import gpt_copies,coherent_a,same_device,check_misc
from synthetic_fixture import baseline
import install
from check_public import public_files
from make_source_release import release

def android():
    h=bytearray(4096);h[:8]=b'ANDROID!';struct.pack_into('<II',h,8,256,0)
    struct.pack_into('<I',h,20,1584);struct.pack_into('<I',h,40,4)
    k=bytearray(256);struct.pack_into('<I',k,4,0x1400000f)
    struct.pack_into('<QQQ',k,8,0,4096,10);k[56:60]=b'ARM\x64'
    return h+k+bytes(4096-len(k))

def dtb():
    names=b'compatible\0';value=b'xiaomi,sheng\0';s=struct.pack('>I',1)+bytes(4)
    s+=struct.pack('>III',3,len(value),0)+value+bytes(-len(value)%4)
    s+=struct.pack('>II',2,9)
    return struct.pack('>10I',0xd00dfeed,56+len(s)+len(names),56,56+len(s),40,17,16,0,len(names),len(s))+bytes(16)+s+names

class Parsers(unittest.TestCase):
    def test_unsafe_slot_halt_cannot_resume_normal_startup(self):
        template=(ROOT/'advanced/slot-switch/init-branch.template.sh').read_text()
        definitions=template.split('if [ -e /proc/device-tree/chosen/sheng,slot-switch ]; then')[0]
        definitions=definitions.replace('> /dev/console','> /dev/null')
        script=definitions+'\nfallback() { return 0; }; sleep() { exit 42; }; slot_halt test; echo NORMAL_STARTUP\n'
        p=subprocess.run(['sh','-c',script],capture_output=True,text=True,timeout=2)
        self.assertEqual(p.returncode,42);self.assertNotIn('NORMAL_STARTUP',p.stdout)
    def test_legacy_bridge_header_and_checksum(self):
        spec=importlib.util.spec_from_file_location('legacy_bridge',ROOT/'advanced/slot-switch/build_legacy_bridge.py')
        legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
        k=b'kernel-test';r=b'ramdisk-test';cmd='x'*600;b=legacy.pack_v0(k,r,cmd)
        self.assertEqual(struct.unpack_from('<10I',b,8),(len(k),0x8000,len(r),0x4000000,0,0,0x1e00000,4096,0,0))
        self.assertEqual((b[64:576]+b[608:1632]).split(b'\0',1)[0],cmd.encode())
        h=hashlib.sha1()
        for p in (k,r,b''):h.update(p);h.update(struct.pack('<I',len(p)))
        self.assertEqual(b[576:596],h.digest());self.assertEqual(b[4096:4096+len(k)],k);self.assertEqual(b[8192:8192+len(r)],r)
    def test_android_roundtrip_and_entry(self):
        r=android_boot(bytes(android()));self.assertEqual(r.entry,64);self.assertEqual(r.extent,4096)
    def test_wrapped_android_is_rejected(self):
        d=android();d[4096+80:4096+101]=b'SHENG-UBOOT-MENU-V1xx'
        with self.assertRaises(Error):android_boot(bytes(d))
    def test_truncated_android_is_rejected(self):
        with self.assertRaises(Error):android_boot(bytes(android()[:4200]))
    def test_fdt_valid_and_bounds(self):
        b=dtb();self.assertEqual(validate_dtb(b),b)
        self.assertEqual(properties(b)[('/','compatible')],b'xiaomi,sheng\0')
        with self.assertRaises(Error):properties(b[:45])
    def test_wrong_board_dtb_rejected(self):
        with self.assertRaises(Error):validate_dtb(dtb().replace(b'xiaomi,sheng',b'xiaomi,other'))
    def test_gpt_header_and_table_corruption(self):
        d=next(iter(baseline()['gpt'].values()));size=d['bytes']
        with tempfile.TemporaryFile() as f:
            f.truncate(size)
            for c in d['copies']:
                h=bytes.fromhex(c['header']);t=bytes.fromhex(c['table'])
                f.seek(struct.unpack_from('<Q',h,24)[0]*4096);f.write(h)
                f.seek(struct.unpack_from('<Q',h,72)[0]*4096);f.write(t)
            f.flush();self.assertEqual(gpt_copies(f.fileno(),size),d['copies'])
            os.pwrite(f.fileno(),b'bad',8192)
            with self.assertRaisesRegex(Error,'table CRC'):gpt_copies(f.fileno(),size)
    def test_incoherent_active_flags_rejected(self):
        g=baseline()['gpt'];coherent_a(g)
        d=next(iter(g.values()));t=bytearray.fromhex(d['copies'][0]['table'])
        struct.pack_into('<Q',t,48,1<<50);d['copies'][0]['table']=t.hex()
        with self.assertRaisesRegex(Error,'ACTIVE'):coherent_a(g)
    def test_pending_ota_or_recovery_refused(self):
        b=bytearray(65536);struct.pack_into('<BIBB',b,32768,2,0x56740ab0,0,0)
        self.assertEqual(check_misc(b),0)
        b[32773]=3
        with self.assertRaisesRegex(Error,'snapshot'):check_misc(b)
        b[32773]=0;b[:13]=b'boot-recovery'
        with self.assertRaisesRegex(Error,'recovery'):check_misc(b)

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.capture=self.root/'capture';self.capture.mkdir();self.candidate=self.root/'candidate';self.candidate.mkdir()
        self.old=b'A'*16384+b'TAIL MUST SURVIVE';self.new=b'N'*4096
        self.node=self.root/'test-block-a';self.node.write_bytes(self.old)
        (self.capture/'boot_a.img').write_bytes(self.old);(self.capture/'boot_b.img').write_bytes(b'B'*1024)
        self.base={'root_uuid':'synthetic-only','gpt':{'synthetic':'unchanged'},'firmware_hashes':{'boot_a':digest(self.node),'boot_b':digest(self.capture/'boot_b.img')},'partitions':{'boot_a':{'path':str(self.node),'partuuid':'synthetic-a','bytes':len(self.old)},'boot_b':{'path':str(self.capture/'boot_b.img'),'partuuid':'synthetic-b','bytes':1024}}}
        (self.capture/'capture.json').write_text(json.dumps(self.base));(self.candidate/'selector.img').write_bytes(self.new)
        self.report={'source_capture_sha256':digest(self.capture/'capture.json'),'wrapper_qa_passed':True,'slot_switch_enabled':False,'image_sha256':hashlib.sha256(self.new).hexdigest(),'image_bytes':len(self.new),'original_android_boot_sha256':hashlib.sha256(self.old).hexdigest(),'full_partition_sha256':hashlib.sha256(self.new+self.old[len(self.new):]).hexdigest()}
        (self.candidate/'report.json').write_text(json.dumps(self.report))
    def tearDown(self):self.tmp.cleanup()
    def snap(self):
        r=json.loads(json.dumps(self.base));r['firmware_hashes']['boot_a']=digest(self.node);return r
    def invoke(self,write=False,restore=False):
        with mock.patch.object(install,'snapshot',side_effect=self.snap):
            install.main(argparse.Namespace(capture=self.capture,candidate=self.candidate,install=write,restore=restore))
    def test_check_only_does_not_write(self):
        self.invoke();self.assertEqual(self.node.read_bytes(),self.old);self.assertFalse((self.capture/'operations').exists())
    def test_prefix_preserves_nonzero_tail_and_restore(self):
        self.invoke(write=True);self.assertEqual(self.node.read_bytes(),self.new+self.old[len(self.new):])
        self.invoke(restore=True);self.assertEqual(self.node.read_bytes(),self.old)
        self.assertEqual((self.capture/'boot_b.img').read_bytes(),b'B'*1024)
    def test_partial_write_rolls_back(self):
        real=os.pwrite;calls=[0]
        def partial(fd,b,offset):
            calls[0]+=1
            if calls[0]==1:return real(fd,b[:len(b)//2],offset)
            if calls[0]==2:raise OSError('injected write failure')
            return real(fd,b,offset)
        with mock.patch.object(install.os,'pwrite',side_effect=partial):
            with self.assertRaises(OSError):self.invoke(write=True)
        self.assertEqual(self.node.read_bytes(),self.old)
    def test_changed_firmware_refuses_before_writes(self):
        self.node.write_bytes(b'foreign'+self.old[7:])
        with self.assertRaisesRegex(Error,'firmware'):self.invoke(write=True)
        self.assertFalse((self.capture/'operations').exists())
    def test_foreign_capture_or_missing_qa_refuses(self):
        for field,value in [('source_capture_sha256','bad'),('wrapper_qa_passed',False),('slot_switch_enabled',True)]:
            with self.subTest(field=field):
                r={**self.report,field:value};(self.candidate/'report.json').write_text(json.dumps(r))
                with self.assertRaises(Error):self.invoke(write=True)
                self.assertEqual(self.node.read_bytes(),self.old)

class PublicationTests(unittest.TestCase):
    def test_private_files_never_enter_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'README.md').write_text('Source only')
            (r/'private').mkdir();(r/'private/boot_a.img').write_bytes(b'ANDROID!private')
            (r/'.build').mkdir();(r/'.build/private-key').write_bytes(b'not for release')
            a=r/'dist/source.zip';self.assertEqual(release(r,a),1)
            with zipfile.ZipFile(a) as z:
                self.assertEqual(set(z.namelist()),{'sheng-boot-menu/README.md','sheng-boot-menu/SOURCE-MANIFEST.sha256'})
    def test_stray_binary_or_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'README.md').write_text('clean');(r/'boot.img').write_bytes(b'ANDROID!')
            with self.assertRaises(Error):public_files(r)
            (r/'boot.img').unlink();(r/'README.md').unlink();(r/'README.md').symlink_to('/etc/hosts')
            with self.assertRaises(Error):public_files(r)
    def test_release_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'README.md').write_text('clean')
            release(r,r/'dist/a.zip');release(r,r/'dist/b.zip')
            self.assertEqual((r/'dist/a.zip').read_bytes(),(r/'dist/b.zip').read_bytes())
    def test_unpacked_source_can_be_checked_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'README.md').write_text('clean');a=r/'dist/a.zip';release(r,a)
            with zipfile.ZipFile(a) as z:z.extractall(r/'dist/unpacked')
            unpacked=r/'dist/unpacked/sheng-boot-menu'
            self.assertEqual([p.name for p in public_files(unpacked)],['README.md'])

if __name__=='__main__':unittest.main()
