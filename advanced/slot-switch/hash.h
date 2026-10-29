/* SPDX-License-Identifier: MIT */
#ifndef SLOT_HASH_H
#define SLOT_HASH_H
#include <stddef.h>
#include <stdint.h>
struct slot_sha256 {
  uint32_t s[8];
  uint64_t bytes;
  unsigned char block[64];
  size_t used;
};
void slot_sha_init(struct slot_sha256 *s);
void slot_sha_update(struct slot_sha256 *s, const void *data, size_t bytes);
void slot_sha_final(struct slot_sha256 *s, unsigned char out[32]);
void slot_sha(const void *data, size_t bytes, unsigned char out[32]);
uint32_t slot_crc(const void *data, size_t bytes);
#endif
