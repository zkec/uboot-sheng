/* SPDX-License-Identifier: GPL-2.0-or-later */
// RAM-only bridge from Android's original handoff to the native U-Boot menu.
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <libfdt.h>

#define HIDDEN __attribute__((visibility("hidden")))
extern unsigned char selector_entry[] HIDDEN, selector_end[] HIDDEN, dt_scratch[] HIDDEN;
extern unsigned char uboot_blob[] HIDDEN, uboot_end[] HIDDEN, fit_blob[] HIDDEN, fit_end[] HIDDEN;
extern unsigned char control_dtb[] HIDDEN, control_dtb_end[] HIDDEN, android_sha256[] HIDDEN;
extern void clean_range(void *, size_t) HIDDEN;
#define UBOOT_BYTES ((uintptr_t)0x300000)
#define FIT_CAPACITY ((uintptr_t)0x2000000)
#define DT_DEST ((uintptr_t)0xb4000000)
#define DT_BYTES ((uintptr_t)0x200000)

static int overlap(uintptr_t a,size_t n,uintptr_t b,size_t m) {
  return n>UINTPTR_MAX-a || m>UINTPTR_MAX-b || (a<b+m && b<a+n);
}

HIDDEN void *prepare_menu(const void *original, uintptr_t android_image) {
  uintptr_t input=(uintptr_t)original, image_end=(uintptr_t)selector_end;
  size_t uboot_size=(size_t)(uboot_end-uboot_blob), fit_size=(size_t)(fit_end-fit_blob);
  int memory, source_memory, chosen, source_chosen, splash, source_splash, len;
  const void *value;
  if ((input&7) || input<0x80000000ULL || input>0x100000000ULL-DT_BYTES) return NULL;
  if ((android_image&0xfff) || android_image<0xa8000000ULL || image_end<=android_image ||
      image_end>0xb0000000ULL || image_end-android_image>0x10000000) return NULL;
  if (overlap(android_image,image_end-android_image,input,DT_BYTES) ||
      overlap(android_image,image_end-android_image,DT_DEST,DT_BYTES)) return NULL;
  if (overlap(input,DT_BYTES,DT_DEST,DT_BYTES)) return NULL;
  if (!uboot_size || uboot_size>UBOOT_BYTES || !fit_size || fit_size>FIT_CAPACITY) return NULL;
  if (fdt_check_header(original)) return NULL;
  len=fdt_totalsize(original);
  if (len<40 || len>(int)DT_BYTES-4096 || fdt_check_full(original,len)) return NULL;
  if (fdt_node_check_compatible(original,0,"qcom,kalama")) return NULL;
  if (fdt_check_full(control_dtb,(size_t)(control_dtb_end-control_dtb))) return NULL;
  if (fdt_open_into(control_dtb,dt_scratch,DT_BYTES)) return NULL;
  memory=fdt_path_offset(dt_scratch,"/memory");
  source_memory=fdt_path_offset(original,"/memory");
  if (memory<0 || source_memory<0) return NULL;
  value=fdt_getprop(original,source_memory,"reg",&len);
  if (!value || len<=0 || len>24*16 || len%16 || fdt_setprop(dt_scratch,memory,"reg",value,len)) return NULL;
  chosen=fdt_path_offset(dt_scratch,"/chosen");
  source_chosen=fdt_path_offset(original,"/chosen");
  if (chosen<0 || source_chosen<0) return NULL;
  value=fdt_getprop(original,source_chosen,"bootargs",&len);
  if (!value || len<=0 || len>8192 || ((const char *)value)[len-1] ||
      fdt_setprop(dt_scratch,chosen,"bootargs",value,len)) return NULL;
  chosen=fdt_path_offset(dt_scratch,"/chosen");
  if (fdt_setprop_u64(dt_scratch,chosen,"linux,initrd-start",(uintptr_t)fit_blob) ||
      fdt_setprop_u64(dt_scratch,chosen,"linux,initrd-end",(uintptr_t)fit_end) ||
      fdt_setprop_u64(dt_scratch,chosen,"sheng,android-image",android_image) ||
      fdt_setprop_u64(dt_scratch,chosen,"sheng,android-fdt",input) ||
      fdt_setprop_u64(dt_scratch,chosen,"sheng,android-image-bytes",ORIGINAL_BYTES) ||
      fdt_setprop(dt_scratch,chosen,"sheng,android-sha256",android_sha256,32)) return NULL;
  splash=fdt_path_offset(dt_scratch,"/reserved-memory/splash_region");
  source_splash=fdt_path_offset(original,"/reserved-memory/splash_region");
  if (splash<0 || source_splash<0) return NULL;
  value=fdt_getprop(original,source_splash,"reg",&len);
  if (!value || len!=16 || fdt_setprop(dt_scratch,splash,"reg",value,len)) return NULL;
  if (fdt_pack(dt_scratch)) return NULL;
  len=fdt_totalsize(dt_scratch);
  if (len<40 || len>(int)DT_BYTES || fdt_check_full(dt_scratch,len)) return NULL;
  // No fallible operation follows the commit. Android's FDT/ramdisk stay intact.
  memcpy((void *)DT_DEST,dt_scratch,len);
  clean_range((void *)DT_DEST,len);
  return (void *)DT_DEST;
}
