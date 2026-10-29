/* Host renderer; SPDX-License-Identifier: GPL-3.0-only */
#include <stdio.h>
#include <stdlib.h>
#include "../assets/menu-render.h"
int main(void)
{
    struct menu_surface s={0,1524,1016,1524,0};
    s.fb=calloc((size_t)s.width*s.height,4);if(!s.fb)return 1;
    sheng_render_menu(&s,0);printf("P6\n%d %d\n255\n",s.width,s.height);
    for(int i=0;i<s.width*s.height;i++) {
        unsigned char rgb[3]={s.fb[i]>>16,s.fb[i]>>8,s.fb[i]};
        fwrite(rgb,1,3,stdout);
    }
    free(s.fb);return ferror(stdout)?1:0;
}
