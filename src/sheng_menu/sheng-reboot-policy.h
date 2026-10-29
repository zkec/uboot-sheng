/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Refuse changed cells; never infer a PMIC register from an unknown DTB. */
static int sheng_reboot_pmic_node(const void *fdt)
{
    int node,cell,parent,pmic,len; const fdt32_t *p;
    if(fdt_check_header(fdt) || fdt_node_check_compatible(fdt,0,"xiaomi,sheng"))return -1;
    node=fdt_path_offset(fdt,"/reboot-mode");
    if(node<0 || fdt_node_check_compatible(fdt,node,"nvmem-reboot-mode"))return -1;
    p=fdt_getprop(fdt,node,"mode-bootloader",&len);
    if(!p || len!=4 || fdt32_to_cpu(*p)!=2)return -1;
    p=fdt_getprop(fdt,node,"nvmem-cells",&len);
    if(!p || len!=4)return -1;
    cell=fdt_node_offset_by_phandle(fdt,fdt32_to_cpu(*p));if(cell<0)return -1;
    p=fdt_getprop(fdt,cell,"reg",&len);
    if(!p || len!=8 || fdt32_to_cpu(p[0])!=0x48 || fdt32_to_cpu(p[1])!=1)return -1;
    p=fdt_getprop(fdt,cell,"bits",&len);
    if(!p || len!=8 || fdt32_to_cpu(p[0])!=1 || fdt32_to_cpu(p[1])!=7)return -1;
    parent=fdt_parent_offset(fdt,cell);
    if(parent<0 || fdt_node_check_compatible(fdt,parent,"qcom,spmi-sdam"))return -1;
    p=fdt_getprop(fdt,parent,"reg",&len);
    if(!p || len!=4 || fdt32_to_cpu(*p)!=0x7100)return -1;
    pmic=fdt_parent_offset(fdt,parent);
    if(pmic<0 || fdt_node_check_compatible(fdt,pmic,"qcom,pm8550"))return -1;
    p=fdt_getprop(fdt,pmic,"reg",&len);
    if(!p || len!=8 || fdt32_to_cpu(p[0])!=0 || fdt32_to_cpu(p[1])!=0)return -1;
    return pmic;
}
