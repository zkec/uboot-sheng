// SPDX-License-Identifier: GPL-2.0-or-later
/* Test-only Android handoff from the preloaded Sheng menu wrapper. */
#include <bootm.h>
#include <command.h>
#include <image.h>
#include <mapmem.h>
#include <malloc.h>
#include <asm/global_data.h>
#include <linux/libfdt.h>
#include <asm/unaligned.h>
#include <u-boot/sha256.h>
#include <env.h>

DECLARE_GLOBAL_DATA_PTR;

static int read_u64(const void *fdt, int node, const char *name, u64 *value)
{
	int len;
	const void *p = fdt_getprop(fdt, node, name, &len);
	if (!p || len != 8)
		return -EINVAL;
	*value = get_unaligned_be64(p);
	return 0;
}

static int do_sheng_android(struct cmd_tbl *cmdtp, int flag, int argc,
			   char *const argv[])
{
	struct bootm_headers handoff = { 0 };
	struct bootm_info bmi;
	u64 image, original_fdt, bytes;
	int node, len;
	const u8 *expected;
	u8 digest[SHA256_SUM_LEN];
	const void *fdt;
	void *handoff_fdt;

	node = fdt_path_offset(gd->fdt_blob, "/chosen");
	if (node < 0 ||
	    read_u64(gd->fdt_blob, node, "sheng,android-image", &image) ||
	    read_u64(gd->fdt_blob, node, "sheng,android-fdt", &original_fdt) ||
	    read_u64(gd->fdt_blob, node, "sheng,android-image-bytes", &bytes)) {
		puts("No verified preloaded Android handoff; reboot to the installed image.\n");
		return CMD_RET_FAILURE;
	}
	if ((image & 0xfff) || image < 0x80000000ULL || image >= 0xb0000000ULL ||
	    bytes < 64 || bytes > 0x6000000 || bytes > 0xb0000000ULL - image ||
	    (original_fdt & 7) || original_fdt < 0x80000000ULL ||
	    original_fdt > 0x100000000ULL - 0x200000)
		return CMD_RET_FAILURE;
	expected = fdt_getprop(gd->fdt_blob, node, "sheng,android-sha256", &len);
	if (!expected || len != SHA256_SUM_LEN)
		return CMD_RET_FAILURE;
	sha256_csum_wd((const u8 *)(uintptr_t)image, bytes, digest, CHUNKSZ_SHA256);
	if (memcmp(digest, expected, SHA256_SUM_LEN)) {
		puts("Android kernel changed in RAM; reboot instead of using it.\n");
		return CMD_RET_FAILURE;
	}
	fdt = (const void *)(uintptr_t)original_fdt;
	if (fdt_check_header(fdt) || fdt_totalsize(fdt) > 0x200000 ||
	    fdt_check_full(fdt, fdt_totalsize(fdt)))
		return CMD_RET_FAILURE;
	/* Keep ABL's source FDT intact. Add only an observation marker to a clone. */
	handoff_fdt = memalign(64, fdt_totalsize(fdt) + 4096);
	if (!handoff_fdt || fdt_open_into(fdt, handoff_fdt, fdt_totalsize(fdt) + 4096))
		return CMD_RET_FAILURE;
	node = fdt_path_offset(handoff_fdt, "/chosen");
	if (node < 0 || fdt_setprop_string(handoff_fdt, node, "sheng,uboot-menu", "menu2-android"))
		return CMD_RET_FAILURE;
	if (env_get("sheng_power_status"))
		fdt_setprop_string(handoff_fdt,node,"sheng,power-status",env_get("sheng_power_status"));
	bootm_init(&bmi);
	bmi.images = &handoff;
	handoff.ep = image;
	handoff.ft_addr = handoff_fdt;
	handoff.ft_len = fdt_totalsize(handoff_fdt);
	handoff.os.arch = IH_ARCH_ARM64;
	bootm_disable_interrupts();
	/* OS_GO performs standard device quiescence and cache/MMU teardown.
	 * Skip OS_PREP: Android's original FDT, bootconfig and ramdisk are retained.
	 */
	do_bootm_linux(BOOTM_STATE_OS_GO, &bmi);
	return CMD_RET_FAILURE;
}

U_BOOT_CMD(sheng_android, 1, 0, do_sheng_android,
	   "boot the verified original Android image already in RAM", "");
