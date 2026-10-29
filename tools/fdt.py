# SPDX-License-Identifier: GPL-3.0-only
"""Bounded FDT reader and control-tree preparation; no device I/O."""
import struct
from common import require,run

def properties(data):
    require(len(data)>=40,'Short device tree')
    h=struct.unpack_from('>10I',data)
    require(h[0]==0xd00dfeed and 40<=h[1]<=len(data),'Invalid FDT size/magic')
    total,pos,strings_start=h[1],h[2],h[3];end=pos+h[9]
    require(40<=pos<end<=total and strings_start+h[8]<=total,'Invalid FDT ranges')
    strings=data[strings_start:strings_start+h[8]];stack=[];out={}
    while pos+4<=end:
        tag=struct.unpack_from('>I',data,pos)[0];pos+=4
        if tag==1:
            nul=data.find(b'\0',pos,end);require(nul>=pos,'Unterminated node')
            stack.append(data[pos:nul].decode());pos=(nul+4)&~3
        elif tag==2:
            require(stack,'Unbalanced node');stack.pop()
        elif tag==3:
            require(pos+8<=end,'Short property');n,off=struct.unpack_from('>II',data,pos);pos+=8
            require(off<len(strings) and pos+n<=end,'Invalid property bounds')
            nul=strings.find(b'\0',off);require(nul>=off and stack,'Invalid property name')
            key=('/'+'/'.join(stack[1:]),strings[off:nul].decode())
            require(key not in out,'Duplicate property');out[key]=data[pos:pos+n];pos=(pos+n+3)&~3
        elif tag==4:pass
        elif tag==9:
            require(not stack,'Unbalanced tree');return out
        else:require(False,'Unknown FDT token')
    require(False,'FDT terminator missing')

def prepare_control(source,cmdline,work,dtc):
    props=properties(source)
    require(b'xiaomi,sheng' in props.get(('/','compatible'),b'').split(b'\0'),'DTB must identify xiaomi,sheng')
    require('/chosen' in {p for p,k in props},'DTB has no chosen node')
    require(0<len(cmdline.encode())<4096 and '\0' not in cmdline and '\n' not in cmdline,'Invalid Linux command line')
    used={struct.unpack('>I',v)[0] for (p,k),v in props.items() if k in ('phandle','linux,phandle') and len(v)==4}
    existing=props.get(('/reserved-memory/splash_region','phandle'))
    phandle=struct.unpack('>I',existing)[0] if existing and len(existing)==4 else 0xff00
    if not existing:
        while phandle in used:phandle+=1
    source_file=work/'linux-source.dtb';source_file.write_bytes(source)
    run([dtc,'-I','dtb','-O','dts','-o',work/'linux-source.dts',source_file])
    escaped=cmdline.replace('\\','\\\\').replace('"','\\"')
    extra='''
/ {
 reserved-memory { splash_region {
  reg=<0 0xb8000000 0 0x2b00000>; no-map; phandle=<PHANDLE>;
 }; };
 chosen {
  #address-cells=<2>; #size-cells=<2>;
  sheng,native-boot="menu3-normal";
  sheng,linux-bootargs="CMDLINE";
  /delete-property/ sheng,slot-switch;
  framebuffer {
   compatible="simple-framebuffer"; memory-region=<PHANDLE>;
   width=<3048>; height=<2032>; stride=<12192>;
   format="a8r8g8b8"; rot=<0>; status="okay";
  };
 };
};
'''.replace('PHANDLE',hex(phandle)).replace('CMDLINE',escaped)
    if any(p.startswith('/gpio-keys') for p,k in props):
        extra+='&{/gpio-keys} { /delete-node/ event-hall-sensor; /delete-node/ event-tablet-mode; };\n'
    (work/'control.dts').write_text((work/'linux-source.dts').read_text()+extra)
    run([dtc,'-I','dts','-O','dtb','-o',work/'control.dtb',work/'control.dts'])
    result=(work/'control.dtb').read_bytes();new=properties(result)
    # Every non-UI property outside these three nodes must remain unchanged.
    def touched(p):return p=='/chosen' or p.startswith('/chosen/framebuffer') or p=='/reserved-memory/splash_region' or p.startswith('/gpio-keys/event-hall-sensor') or p.startswith('/gpio-keys/event-tablet-mode')
    require({k:v for k,v in props.items() if not touched(k[0])}=={k:v for k,v in new.items() if not touched(k[0])},'Unexpected control DTB modification')
    return result
