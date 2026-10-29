/* SPDX-License-Identifier: GPL-2.0-or-later */
// Freestanding support for the existing libfdt code, with no SIMD or heap.
#include <string.h>
void *memcpy(void *dst, const void *src, size_t n) {
  unsigned char *d=dst; const unsigned char *s=src;
  while(n--) *d++=*s++;
  return dst;
}
void *memmove(void *dst, const void *src, size_t n) {
  unsigned char *d=dst; const unsigned char *s=src;
  if(d>s && d<s+n) { d+=n; s+=n; while(n--) *--d=*--s; }
  else while(n--) *d++=*s++;
  return dst;
}
void *memset(void *dst, int v, size_t n) {
  unsigned char *d=dst; while(n--) *d++=(unsigned char)v; return dst;
}
void *memchr(const void *src, int v, size_t n) {
  const unsigned char *s=src;
  while(n--) { if(*s==(unsigned char)v) return (void *)s; s++; }
  return NULL;
}
int memcmp(const void *a, const void *b, size_t n) {
  const unsigned char *x=a,*y=b;
  while(n--) { if(*x!=*y) return (int)*x-(int)*y; x++; y++; }
  return 0;
}
size_t strlen(const char *s) { size_t n=0; while(s[n]) n++; return n; }
size_t strnlen(const char *s,size_t m) { size_t n=0; while(n<m && s[n]) n++; return n; }
char *strrchr(const char *s,int c) {
  const char *last=NULL; do { if(*s==(char)c) last=s; } while(*s++);
  return (char *)last;
}
