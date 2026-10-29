/* SPDX-License-Identifier: GPL-2.0-or-later */
.section .text.entry,"ax"
.global selector_entry
selector_entry:
  mov x20,x0
  mov x21,x1
  mov x22,x2
  mov x23,x3
  mov x24,sp
  adr x25,selector_entry
  ldr x17,=SELECTOR_OFFSET
  sub x26,x25,x17
  // Restore the original Android header in RAM before either handoff.
  ldr w17,=ORIGINAL_CODE1
  str w17,[x26,#4]
  ldr x17,=SELECTOR_OFFSET
  str x17,[x26,#16]
  mov x0,x26
  mov x1,#64
  bl clean_range
  dsb sy
  ic iallu
  dsb sy
  isb
  // Only the already-qualified, MMU-off AArch64 handoff is supported.
  mrs x4,CurrentEL
  cmp x4,#4
  b.eq 1f
  cmp x4,#8
  b.ne android_entry
  mrs x4,sctlr_el2
  b 2f
1:mrs x4,sctlr_el1
2:tbnz x4,#0,android_entry
  tst x26,#0xfff
  b.ne android_entry
  adrp x16,stack_end
  add x16,x16,:lo12:stack_end
  mov sp,x16
  mov x0,x20
  mov x1,x26
  bl prepare_menu
  cbz x0,android_entry
  // U-Boot and FIT are already loaded and run in place; only FDT was copied.
  mov x1,xzr
  mov x2,xzr
  mov x3,xzr
  adrp x16,uboot_blob
  add x16,x16,:lo12:uboot_blob
  dsb sy
  ic iallu
  dsb sy
  isb
  br x16

android_entry:
  mov x0,x20
  mov x1,x21
  mov x2,x22
  mov x3,x23
  mov sp,x24
  ldr x16,=ORIGINAL_ENTRY_OFFSET
  add x16,x26,x16
  br x16

.global clean_range
.hidden clean_range
clean_range:
  add x2,x0,x1
  mrs x3,ctr_el0
  ubfx x3,x3,#16,#4
  mov x4,#4
  lsl x4,x4,x3
  sub x5,x4,#1
  bic x0,x0,x5
3:dc cvac,x0
  add x0,x0,x4
  cmp x0,x2
  b.lo 3b
  dsb sy
  ret

.section .rodata,"a"
.asciz "SHENG-UBOOT-MENU-V1"
