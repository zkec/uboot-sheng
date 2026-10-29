// SPDX-License-Identifier: GPL-2.0-or-later
#include <command.h>
#include <cli.h>
#include <dm.h>
#include <asm/global_data.h>
#include <linux/libfdt.h>
#include <linux/psci.h>
#include <linux/string.h>
#include <power/pmic.h>
#include <sysreset.h>
#include <env.h>
#include "sheng-reboot-policy.h"
DECLARE_GLOBAL_DATA_PTR;

static int power_failure(const char *stage,int error)
{
    char status[96];snprintf(status,sizeof(status),"%s (%d)",stage,error);
    env_set("sheng_power_status",status);puts(status);puts("\n");return CMD_RET_FAILURE;
}

static int do_sheng_power(struct cmd_tbl *cmdtp,int flag,int argc,char *const argv[])
{
    struct udevice *pmic,*firmware; int node,old,value,ret,back;char path[256];
    if(argc!=2)return CMD_RET_USAGE;
    ret=uclass_get_device_by_ofnode(UCLASS_FIRMWARE,ofnode_path("/psci"),&firmware);
    if(ret)return power_failure("psci-probe",ret);
    if(!strcmp(argv[1],"poweroff")) {ret=run_command("poweroff",0);return power_failure("poweroff-returned",ret);}
    if(!strcmp(argv[1],"restart")) {psci_sys_reset(SYSRESET_COLD);return power_failure("restart-returned",(int)invoke_psci_fn(PSCI_0_2_FN_PSCI_VERSION,0,0,0));}
    if(strcmp(argv[1],"bootloader"))return CMD_RET_USAGE;
    node=sheng_reboot_pmic_node(gd->fdt_blob);
    if(node<0)return power_failure("bootloader-cell-guard",node);
    ret=fdt_get_path(gd->fdt_blob,node,path,sizeof(path));
    if(ret)return power_failure("bootloader-pmic-path",ret);
    /* Resolve by path in either live OF or flat OF; a flat offset alone is
     * deliberately an invalid ofnode when CONFIG_OF_LIVE is active. */
    ret=uclass_get_device_by_ofnode(UCLASS_PMIC,ofnode_path(path),&pmic);
    if(ret)return power_failure("bootloader-pmic-probe",ret);
    old=pmic_reg_read(pmic,0x7148);if(old<0)return power_failure("bootloader-pmic-read",old);
    /* Same one-byte bits<1,7> cell used by Linux's nvmem reboot-mode driver.
     * Keep bit 0; RTC offset and all other PMIC registers remain untouched. */
    value=(old&1)|(2<<1);
    ret=pmic_reg_write(pmic,0x7148,value);
    back=pmic_reg_read(pmic,0x7148);
    if(ret || back!=value) {
        pmic_reg_write(pmic,0x7148,old);return power_failure(ret?"bootloader-pmic-write":"bootloader-readback",ret?ret:back);
    }
    psci_sys_reset(SYSRESET_COLD);
    /* If firmware rejects reset, undo the request before returning to menu. */
    pmic_reg_write(pmic,0x7148,old);
    return power_failure("bootloader-cold-reset-returned",(int)invoke_psci_fn(PSCI_0_2_FN_PSCI_VERSION,0,0,0));
}
U_BOOT_CMD(sheng_power,2,0,do_sheng_power,"restart, enter Xiaomi fastboot, or power off","restart|bootloader|poweroff");
