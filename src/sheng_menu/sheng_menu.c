// SPDX-License-Identifier: GPL-2.0-or-later
#include <cli.h>
#include <command.h>
#include <dm.h>
#include <env.h>
#include <malloc.h>
#include <menu.h>
#include <video.h>
#include <button.h>
#include <linux/delay.h>
#include <linux/input.h>
#include <linux/string.h>
#include <watchdog.h>
#include "sheng-menu-ui/build-options.h"
#include "sheng-menu-ui/menu-render.h"

static void wait_power_release(void)
{
    struct udevice *dev; bool down;
    do {
        down=false;
        uclass_foreach_dev_probe(UCLASS_BUTTON,dev) {
            int code=button_get_code(dev);
            if((code==KEY_POWER || code==KEY_ENTER) && button_get_state(dev)==BUTTON_ON)down=true;
        }
        if(down){schedule();mdelay(10);}
    } while(down);
    while(tstc())getchar();
}

static int do_sheng_menu(struct cmd_tbl *cmdtp,int flag,int argc,char *const argv[])
{
    struct udevice *dev;
    struct video_priv *p;
    struct menu_surface surface;
    struct cli_ch_state input;
    struct bootmenu_data menu = { .delay=-1, .count=SHENG_MENU_SLOT_SWITCH ? 6 : 5 };
    const char *choices[]={"android","native","restart","bootloader","poweroff","slotswitch"};
    const char *commands[]={"sheng_android","run native","sheng_power restart","sheng_power bootloader","sheng_power poweroff","run slotswitch"};
    enum bootmenu_key key;
    char *oldout, *olderr;
    int selected=0;bool failed=false;
    if(uclass_first_device_err(UCLASS_VIDEO,&dev))return CMD_RET_FAILURE;
    p=dev_get_uclass_priv(dev);
    if(p->bpix!=VIDEO_BPP32 || p->rot || p->ysize<500 || p->ysize>4096 ||
       p->xsize<menu_scale(&(struct menu_surface){.height=p->ysize},1524) ||
       p->line_length<p->xsize*4 || p->line_length%4 ||
       p->fb_size<(unsigned long)p->line_length*p->ysize ||
       (p->format!=VIDEO_X8R8G8B8 && p->format!=VIDEO_X8B8G8R8))return CMD_RET_FAILURE;
    surface=(struct menu_surface){p->fb,p->xsize,p->ysize,p->line_length/4,p->format==VIDEO_X8B8G8R8};
    oldout=strdup(env_get("stdout")?:"serial,vidconsole");
    olderr=strdup(env_get("stderr")?:"serial,vidconsole");
    if(!oldout || !olderr){free(oldout);free(olderr);return CMD_RET_FAILURE;}
    env_set("stdout","serial");env_set("stderr","serial");
    cli_ch_init(&input);
    for(;;) {
        sheng_render_menu(&surface,selected);
        if(failed)menu_mask(&surface,&art_action_failed,762,931,0xf48b9d);
        video_damage(dev,0,0,p->xsize,p->ysize);video_sync(dev,true);
        do {key=bootmenu_loop(&menu,&input);} while(key==BKEY_NONE);
        if(key==BKEY_UP)selected=(selected+menu.count-1)%menu.count;
        else if(key==BKEY_DOWN)selected=(selected+1)%menu.count;
        else if(key==BKEY_SHORTCUT && input.shortcut_key>=0 && input.shortcut_key<menu.count){selected=input.shortcut_key;key=BKEY_SELECT;}
        if(key!=BKEY_SELECT)continue;
        wait_power_release();cli_ch_init(&input);
        env_set("sheng_choice",choices[selected]);
        env_set("stdout",oldout);env_set("stderr",olderr);
        run_command(commands[selected],0);
        env_set("stdout","serial");env_set("stderr","serial");
        failed=true;
    }
}
U_BOOT_CMD(sheng_menu,1,0,do_sheng_menu,"choose Android or Linux with Volume and Power","");
