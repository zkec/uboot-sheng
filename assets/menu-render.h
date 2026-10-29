/* SPDX-License-Identifier: GPL-2.0-or-later */
/* The same framebuffer renderer is compiled into U-Boot and the host preview. */
#include "menu-art.h"
#ifndef SHENG_MENU_SLOT_SWITCH
#define SHENG_MENU_SLOT_SWITCH 0
#endif
struct menu_surface { unsigned int *fb; int width, height, stride, bgr; };
static unsigned int menu_color(struct menu_surface *s, unsigned int c)
{
    return s->bgr ? ((c&255)<<16)|(c&0xff00)|((c>>16)&255) : c;
}
static void menu_rect(struct menu_surface *s,int x,int y,int w,int h,unsigned int color)
{
    int i,j; color=menu_color(s,color);
    if (x<0 || y<0 || w<0 || h<0 || x>s->width-w || y>s->height-h) return;
    for(j=y;j<y+h;j++) for(i=x;i<x+w;i++) s->fb[j*s->stride+i]=color;
}
static int menu_scale(struct menu_surface *s,int n) { return n*s->height/1016; }
static void menu_box(struct menu_surface *s,int x,int y,int w,int h,unsigned int c)
{
    menu_rect(s,(s->width-menu_scale(s,1524))/2+menu_scale(s,x),menu_scale(s,y),menu_scale(s,w),menu_scale(s,h),c);
}
static void menu_mask(struct menu_surface *s,const struct menu_art *a,int cx,int y,unsigned int color)
{
    unsigned char *mask=malloc(a->w*a->h); unsigned int n=0,i,k; int x,j,w,h,left,top;
    if(!mask)return;
    for(i=0;i+2<a->bytes;i+=3) {
        unsigned int count=a->data[i]|(a->data[i+1]<<8);
        if(count>(unsigned int)(a->w*a->h)-n){free(mask);return;}
        for(k=0;k<count;k++)mask[n++]=a->data[i+2];
    }
    if(n!=(unsigned int)(a->w*a->h)){free(mask);return;}
    w=menu_scale(s,a->w)/2;h=menu_scale(s,a->h)/2;
    left=(s->width-menu_scale(s,1524))/2+menu_scale(s,cx)-w/2;top=menu_scale(s,y);
    color=menu_color(s,color);
    if(w<=0 || h<=0 || left<0 || top<0 || left>s->width-w || top>s->height-h){free(mask);return;}
    for(j=0;j<h;j++)for(x=0;x<w;x++) {
        unsigned int alpha=mask[(j*a->h/h)*a->w+x*a->w/w],old,r,g,b;
        unsigned int *pixel=&s->fb[(top+j)*s->stride+left+x];
        if(!alpha)continue;
        old=*pixel;
        r=(((color>>16)&255)*alpha+((old>>16)&255)*(255-alpha))/255;
        g=(((color>>8)&255)*alpha+((old>>8)&255)*(255-alpha))/255;
        b=((color&255)*alpha+(old&255)*(255-alpha))/255;
        *pixel=(r<<16)|(g<<8)|b;
    }
    free(mask);
}
static void menu_color_icon(struct menu_surface *s,const struct menu_color_art *a,int cx,int y)
{
    unsigned int *pixels=malloc(a->w*a->h*4),n=0,i,k;int x,j,w,h,left,top;
    if(!pixels)return;
    for(i=0;i+5<a->bytes;i+=6) {
        unsigned int count=a->data[i]|(a->data[i+1]<<8);
        unsigned int rgba=((unsigned int)a->data[i+2]<<24)|((unsigned int)a->data[i+3]<<16)|
                          ((unsigned int)a->data[i+4]<<8)|a->data[i+5];
        if(count>(unsigned int)(a->w*a->h)-n){free(pixels);return;}
        for(k=0;k<count;k++)pixels[n++]=rgba;
    }
    if(n!=(unsigned int)(a->w*a->h)){free(pixels);return;}
    w=menu_scale(s,a->w)/2;h=menu_scale(s,a->h)/2;
    left=(s->width-menu_scale(s,1524))/2+menu_scale(s,cx)-w/2;top=menu_scale(s,y);
    if(w<=0 || h<=0 || left<0 || top<0 || left>s->width-w || top>s->height-h){free(pixels);return;}
    for(j=0;j<h;j++)for(x=0;x<w;x++) {
        unsigned int rgba=pixels[(j*a->h/h)*a->w+x*a->w/w],alpha=rgba&255;
        unsigned int color=menu_color(s,rgba>>8),*pixel=&s->fb[(top+j)*s->stride+left+x],old=*pixel,r,g,b;
        if(!alpha)continue;
        r=(((color>>16)&255)*alpha+((old>>16)&255)*(255-alpha))/255;
        g=(((color>>8)&255)*alpha+((old>>8)&255)*(255-alpha))/255;
        b=((color&255)*alpha+(old&255)*(255-alpha))/255;
        *pixel=(r<<16)|(g<<8)|b;
    }
    free(pixels);
}
static void sheng_render_menu(struct menu_surface *s,int selected)
{
    int i,center;
    menu_rect(s,0,0,s->width,s->height,0x000000);
    menu_mask(s,&art_title,762,214,0xf5f5f5);
    for(i=0;i<2;i++) {
        int x=406+i*372;center=x+170;
        menu_box(s,x,327,340,382,i==selected?0xe5e5e5:0x303030);
        menu_box(s,x+2,329,336,378,i==selected?0x181818:0x0c0c0c);
        if(i)menu_color_icon(s,&art_tux,center,381);
        else menu_mask(s,&art_android_icon,center,381,0x3ddc84);
        menu_mask(s,i?&art_linux:&art_android,center,538,0xf5f5f5);
        if(i==selected) {
            menu_box(s,center-55,651,110,28,0x303030);
            menu_mask(s,&art_selected,center,659,0xe0e0e0);
        }
    }
    for(i=0;i<(SHENG_MENU_SLOT_SWITCH ? 4 : 3);i++) {
        int x=(SHENG_MENU_SLOT_SWITCH ? 406 : 497)+i*181;
        const struct menu_art *a=i==0?&art_restart:i==1?&art_bootloader:i==2?&art_poweroff:&art_slotswitch;
        menu_box(s,x,744,169,58,selected==i+2?0xe5e5e5:0x303030);
        menu_box(s,x+2,746,165,54,selected==i+2?0x181818:0x0c0c0c);
        menu_mask(s,a,x+84,764,selected==i+2?0xf5f5f5:0xb5b5b5);
    }
    menu_box(s,494,853,200,35,0x202020);
    menu_mask(s,&art_choose,594,864,0xd4d4d4);
    menu_mask(s,&art_choose_sub,735,864,0x909090);
    menu_box(s,814,853,90,35,0x202020);
    menu_mask(s,&art_confirm,859,864,0xd4d4d4);
    menu_mask(s,&art_confirm_sub,950,864,0x909090);
}
