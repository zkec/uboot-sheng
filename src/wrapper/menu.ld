/* SPDX-License-Identifier: GPL-2.0-or-later */
ENTRY(selector_entry)
SECTIONS {
  . = 0;
  .text : { KEEP(*(.text.entry)) *(.text*) }
  .rodata : { *(.rodata*) }
  .data : { *(.data*) }
  .bss : { *(.bss*) *(COMMON) }
  . = ALIGN(16);
  .stack (NOLOAD) : { . += 0x10000; }
  stack_end = .;
  . = ALIGN(4096);
  dt_scratch = .;
  .dt (NOLOAD) : { . += 0x200000; }
  . = ALIGN(4096);
  .uboot : {
    uboot_blob = .; KEEP(*(.ubootpayload)) uboot_end = .;
    . = uboot_blob + 0x300000;
  }
  .payload : {
    . = ALIGN(16);
    control_dtb = .; KEEP(*(.dtbpayload)) control_dtb_end = .;
    . = ALIGN(16);
    android_sha256 = .; KEEP(*(.hashpayload))
    . = ALIGN(4096);
    fit_blob = .; KEEP(*(.fitpayload)) fit_end = .;
  }
  selector_end = .;
  /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
