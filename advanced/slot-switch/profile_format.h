/* SPDX-License-Identifier: MIT */
#ifndef SHENG_SLOT_PROFILE_FORMAT_H
#define SHENG_SLOT_PROFILE_FORMAT_H

/* All integers are little endian, decoded byte-wise (no packed C casts).
 * Header 80 bytes:
 *   magic[16], version:u32, total_bytes:u32, disks:u32, hashes:u32,
 *   ext4_uuid[16], sha256_of_payload[32].
 * Each of exactly six variable disk records:
 *   disk_uuid[16] (GPT byte order), disk_bytes:u64, block_size:u32,
 *   entry_count:u32, entry_size:u32, reserved_zero:u32,
 *   primary_header[block_size], backup_header[block_size],
 *   canonical_source_A_entries[entry_count * entry_size].
 * Then 34..128 firmware hash records, including the 34 mandatory core records
 * and full stored boot A/B (additional static paired images may be pinned):
 *   partition_name[36] (NUL terminated ASCII), partition_bytes:u64,
 *   sha256[32].
 * Source A is canonical only for type-GUID/layout comparison. At runtime all
 * AB flags are validated against the actual active slot; a B source is valid.
 * Profile is external data, installed only AFTER final image hash is known.
 */
#define SLOT_PROFILE_MAGIC "SHENG-SLOT-V2\0\0\0\0"
#define SLOT_PROFILE_VERSION 2U
#define SLOT_PROFILE_HEADER_BYTES 80U
#define SLOT_PROFILE_MAX_BYTES (128U * 1024U)
#define SLOT_MAX_DISKS 6U
#define SLOT_MAX_ENTRIES 128U
#define SLOT_ENTRY_BYTES 128U
#define SLOT_BLOCK_BYTES 4096U
#define SLOT_FIRMWARE_HASHES 34U
#define SLOT_MAX_FIRMWARE_HASHES 128U
#define SLOT_HASH_RECORD_BYTES 76U
#define SLOT_DISK_RECORD_FIXED_BYTES 40U

#define SLOT_EXIT_REFUSED 10
#define SLOT_EXIT_ROLLED_BACK 20
#define SLOT_EXIT_UNSAFE 30

#define SLOT_ATTR_ACTIVE (1ULL << 50)
#define SLOT_ATTR_SUCCESS (1ULL << 54)
#define SLOT_ATTR_UNBOOTABLE (1ULL << 55)
#define SLOT_ATTR_AB_MASK (0xffULL << 48)
#define SLOT_ATTR_PRIORITY (3ULL << 48)
#define SLOT_ATTR_RETRY (0x38ULL << 48)

#endif
