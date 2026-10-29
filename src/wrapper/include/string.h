/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef SHENG_SELECTOR_STRING_H
#define SHENG_SELECTOR_STRING_H
#include <stddef.h>
void *memcpy(void *, const void *, size_t);
void *memmove(void *, const void *, size_t);
void *memset(void *, int, size_t);
void *memchr(const void *, int, size_t);
int memcmp(const void *, const void *, size_t);
size_t strlen(const char *);
size_t strnlen(const char *, size_t);
char *strrchr(const char *, int);
#endif
