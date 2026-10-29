/* SPDX-License-Identifier: MIT
 * Self-contained native initramfs slot action. No qbootctl, Python or updater.
 * Device access is impossible in --mock mode. Mock writes are test-build only.
 */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include "hash.h"
#include "profile_format.h"
#include <ctype.h>
#include <dirent.h>
#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/bsg.h>
#include <linux/fs.h>
#include <linux/magic.h>
#include <scsi/scsi_bsg_ufs.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/statvfs.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

#define MAX_PARTS (SLOT_MAX_DISKS * SLOT_MAX_ENTRIES)
#define ARRAY_BYTES (SLOT_MAX_ENTRIES * SLOT_ENTRY_BYTES)
#define COPY_BYTES (SLOT_BLOCK_BYTES + ARRAY_BYTES)
#define FW_MAX SLOT_MAX_FIRMWARE_HASHES
#define NVMEM_OFFSET 0x48
#define MOCK_MARKER "SHENG-SLOT-MOCK-V2\n"

struct copy {
  unsigned char h[SLOT_BLOCK_BYTES], t[ARRAY_BYTES];
};
struct disk {
  unsigned char uuid[16];
  uint64_t bytes;
  unsigned count, table_bytes;
  const unsigned char *template_h[2], *canonical;
  char path[PATH_MAX];
  int fd;
  bool changed, touched;
  struct copy old[2], next[2];
};
struct part {
  char name[36];
  unsigned disk, index;
  int partner;
  uint64_t start, end;
};
struct fw_hash {
  char name[36];
  uint64_t bytes;
  unsigned char hash[32];
};
struct context {
  bool mock, dry, test_apply, started, root_mounted, nvmem_attempted;
  char root[PATH_MAX], mockdir[PATH_MAX], profile_path[PATH_MAX],
      plan_path[PATH_MAX];
  char error[512], nvmem_path[PATH_MAX], root_device[PATH_MAX],
      transaction[PATH_MAX];
  unsigned char *blob, fsuuid[16], profile_sha[32], old_nvmem;
  size_t blob_bytes;
  unsigned hash_count, part_count;
  char source, target;
  struct disk disks[SLOT_MAX_DISKS];
  struct part parts[MAX_PARTS];
  struct fw_hash hashes[FW_MAX];
  int bsg_fd, nvmem_fd;
  unsigned old_lun;
  unsigned long write_count;
  struct stat mock_lun_stat, mock_nvmem_stat;
#ifdef SLOT_TEST_BUILD
  long fault_at;
  bool persistent_fault;
#endif
};

static uint32_t r32(const void *v) {
  const unsigned char *p = v;
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
         ((uint32_t)p[3] << 24);
}
static uint64_t r64(const void *v) {
  const unsigned char *p = v;
  return (uint64_t)r32(p) | ((uint64_t)r32(p + 4) << 32);
}
static uint32_t b32(const void *v) {
  const unsigned char *p = v;
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
         ((uint32_t)p[2] << 8) | p[3];
}
static void w32(void *v, uint32_t n) {
  unsigned char *p = v;
  for (unsigned i = 0; i < 4; i++)
    p[i] = (unsigned char)(n >> (8 * i));
}
static void w64(void *v, uint64_t n) {
  unsigned char *p = v;
  for (unsigned i = 0; i < 8; i++)
    p[i] = (unsigned char)(n >> (8 * i));
}
static bool zero16(const void *v) {
  static const unsigned char zero[16];
  return !memcmp(v, zero, 16);
}
static int errorf(struct context *c, const char *fmt, ...) {
  va_list a;
  va_start(a, fmt);
  vsnprintf(c->error, sizeof(c->error), fmt, a);
  va_end(a);
  return -1;
}
static void stage(struct context *c, const char *message, bool visible) {
  fprintf(stderr, "slot-helper: %s\n", message);
  if (!c->mock && visible) {
    int fd =
        open("/dev/console", O_WRONLY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW);
    if (fd >= 0) {
      struct stat st;
      if (!fstat(fd, &st) && S_ISCHR(st.st_mode)) {
        ssize_t ignored = write(fd, message, strlen(message));
        (void)ignored;
        ignored = write(fd, "\n", 1);
        (void)ignored;
      }
      close(fd);
    }
  }
}
#define NEED(c, condition, ...)                                                \
  do {                                                                         \
    if (!(condition))                                                          \
      return errorf((c), __VA_ARGS__);                                         \
  } while (0)
static int full_read(struct context *c, int fd, void *buf, size_t n,
                     uint64_t off) {
  unsigned char *p = buf;
  size_t done = 0;
  while (done < n) {
    ssize_t k = pread(fd, p + done, n - done, (off_t)(off + done));
    if (k < 0 && errno == EINTR)
      continue;
    if (k <= 0)
      return errorf(c, "short read at %llu: %s",
                    (unsigned long long)(off + done), strerror(errno));
    done += (size_t)k;
  }
  return 0;
}
static void hex(const unsigned char *p, size_t n, char *out) {
  static const char a[] = "0123456789abcdef";
  for (size_t i = 0; i < n; i++) {
    out[2 * i] = a[p[i] >> 4];
    out[2 * i + 1] = a[p[i] & 15];
  }
  out[2 * n] = 0;
}
static int pathf(struct context *c, char *out, size_t cap, const char *fmt,
                 ...) {
  va_list a;
  va_start(a, fmt);
  int n = vsnprintf(out, cap, fmt, a);
  va_end(a);
  NEED(c, n >= 0 && (size_t)n < cap, "path exceeds bound");
  return 0;
}
static int suffix(const char *n) {
  size_t l = strlen(n);
  return l >= 2 && n[l - 2] == '_' && (n[l - 1] == 'a' || n[l - 1] == 'b')
             ? n[l - 1]
             : 0;
}
static int part_index(struct context *c, const char *n) {
  for (unsigned i = 0; i < c->part_count; i++)
    if (!strcmp(c->parts[i].name, n))
      return (int)i;
  return -1;
}
static unsigned char *entry(struct context *c, const struct part *p,
                            unsigned copy, bool next) {
  struct disk *d = &c->disks[p->disk];
  return (next ? d->next[copy].t : d->old[copy].t) + p->index * 128;
}
static int label(struct context *c, const unsigned char *e, char out[36]) {
  unsigned i;
  for (i = 0; i < 36; i++) {
    unsigned v = e[56 + 2 * i] | ((unsigned)e[57 + 2 * i] << 8);
    if (!v)
      break;
    NEED(c,
         i < 35 && v < 128 &&
             (isalnum((int)v) || v == '_' || v == '-' || v == '.'),
         "unsupported partition label");
    out[i] = (char)v;
  }
  NEED(c, i > 0 && i < 36, "empty or oversized partition label");
  out[i] = 0;
  for (unsigned j = i; j < 36; j++)
    NEED(c, e[56 + 2 * j] == 0 && e[57 + 2 * j] == 0,
         "nonzero data after partition name");
  return 0;
}
static bool header_crc(const unsigned char *h) {
  unsigned char tmp[92];
  if (memcmp(h, "EFI PART", 8) || r32(h + 8) != 0x10000 || r32(h + 12) != 92 ||
      r32(h + 20) != 0)
    return false;
  memcpy(tmp, h, 92);
  w32(tmp + 16, 0);
  return slot_crc(tmp, 92) == r32(h + 16);
}
static void header_recalc(unsigned char *h, const unsigned char *t, size_t n) {
  w32(h + 88, slot_crc(t, n));
  w32(h + 16, 0);
  w32(h + 16, slot_crc(h, 92));
}
static bool header_same_geometry(const unsigned char *a,
                                 const unsigned char *b) {
  unsigned char x[SLOT_BLOCK_BYTES], y[SLOT_BLOCK_BYTES];
  memcpy(x, a, sizeof(x));
  memcpy(y, b, sizeof(y));
  w32(x + 16, 0);
  w32(y + 16, 0);
  w32(x + 88, 0);
  w32(y + 88, 0);
  return !memcmp(x, y, sizeof(x));
}

static int checked_profile_open(struct context *c) {
  int fd = -1, dir = -1;
  struct stat st;
  if (c->mock) {
    fd = open(c->profile_path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
  } else {
    dir = open(c->root, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    NEED(c, dir >= 0, "open root: %s", strerror(errno));
    const char *names[] = {"var", "lib", "sheng-boot-menu"};
    for (unsigned i = 0; i < 3; i++) {
      int next = openat(dir, names[i],
                        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
      close(dir);
      dir = next;
      if (dir < 0)
        return errorf(c, "profile directory unavailable: %s", strerror(errno));
      if (fstat(dir, &st) || st.st_uid != 0 || (st.st_mode & 0022)) {
        close(dir);
        return errorf(c, "insecure profile directory");
      }
    }
    fd = openat(dir, "profile.bin",
                O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    close(dir);
  }
  NEED(c, fd >= 0, "open profile: %s", strerror(errno));
  if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_nlink != 1 ||
      (!c->mock && (st.st_uid != 0 || (st.st_mode & 0022))) ||
      st.st_size < 80 || st.st_size > SLOT_PROFILE_MAX_BYTES) {
    close(fd);
    return errorf(c, "insecure or oversized profile");
  }
  c->blob_bytes = (size_t)st.st_size;
  c->blob = malloc(c->blob_bytes);
  if (!c->blob) {
    close(fd);
    return errorf(c, "profile allocation failed");
  }
  int ret = full_read(c, fd, c->blob, c->blob_bytes, 0);
  close(fd);
  return ret;
}
static int parse_profile(struct context *c) {
  const unsigned char *p = c->blob, *end = p + c->blob_bytes;
  unsigned char digest[32];
  NEED(c,
       !memcmp(p, SLOT_PROFILE_MAGIC, 16) && r32(p + 16) == 2 &&
           r32(p + 20) == c->blob_bytes && r32(p + 24) == 6,
       "invalid profile header");
  c->hash_count = r32(p + 28);
  NEED(c, c->hash_count >= SLOT_FIRMWARE_HASHES && c->hash_count <= FW_MAX,
       "unexpected firmware hash count");
  memcpy(c->fsuuid, p + 32, 16);
  slot_sha(p + 80, c->blob_bytes - 80, digest);
  NEED(c, !memcmp(digest, p + 48, 32), "profile payload checksum failed");
  slot_sha(p, c->blob_bytes, c->profile_sha);
  p += 80;
  for (unsigned i = 0; i < SLOT_MAX_DISKS; i++) {
    struct disk *d = &c->disks[i];
    d->fd = -1;
    NEED(c, (size_t)(end - p) >= 40, "truncated disk profile");
    memcpy(d->uuid, p, 16);
    d->bytes = r64(p + 16);
    unsigned bs = r32(p + 24);
    d->count = r32(p + 28);
    NEED(c,
         !zero16(d->uuid) && bs == 4096 && d->count > 0 && d->count <= 128 &&
             r32(p + 32) == 128 && r32(p + 36) == 0,
         "unsupported disk profile");
    NEED(c,
         d->bytes >= 14 * 4096 && d->bytes % 4096 == 0 &&
             d->bytes < (1ULL << 54),
         "invalid disk capacity");
    d->table_bytes = d->count * 128;
    p += 40;
    NEED(c, (size_t)(end - p) >= 8192 + d->table_bytes,
         "truncated GPT profile");
    d->template_h[0] = p;
    d->template_h[1] = p + 4096;
    d->canonical = p + 8192;
    p += 8192 + d->table_bytes;
    for (unsigned copy = 0; copy < 2; copy++) {
      const unsigned char *h = d->template_h[copy];
      uint64_t lba = copy ? d->bytes / 4096 - 1 : 1,
               alt = copy ? 1 : d->bytes / 4096 - 1, table = r64(h + 72);
      NEED(c,
           header_crc(h) && r64(h + 24) == lba && r64(h + 32) == alt &&
               !memcmp(h + 56, d->uuid, 16) && r32(h + 80) == d->count &&
               r32(h + 84) == 128,
           "invalid profile GPT header");
      NEED(c,
           r64(h + 40) >= 2 && r64(h + 40) <= r64(h + 48) &&
               r64(h + 48) < d->bytes / 4096 - 1,
           "invalid profile usable bounds");
      NEED(c,
           table >= 2 && table < d->bytes / 4096 - 1 &&
               d->table_bytes <= (d->bytes / 4096 - 1 - table) * 4096 &&
               slot_crc(d->canonical, d->table_bytes) == r32(h + 88),
           "invalid profile table bounds/CRC");
    }
    NEED(c,
         r64(d->template_h[0] + 40) == r64(d->template_h[1] + 40) &&
             r64(d->template_h[0] + 48) == r64(d->template_h[1] + 48),
         "profile header bounds disagree");
    NEED(c,
         r64(d->template_h[0] + 72) * 4096 + d->table_bytes <=
             r64(d->template_h[1] + 72) * 4096,
         "profile GPT arrays overlap");
    for (unsigned j = 0; j < i; j++)
      NEED(c, memcmp(c->disks[j].uuid, d->uuid, 16),
           "duplicate profile disk UUID");
    for (unsigned e = 0; e < d->count; e++) {
      const unsigned char *row = d->canonical + e * 128;
      if (zero16(row))
        continue;
      NEED(c, c->part_count < MAX_PARTS, "partition count exceeds bound");
      struct part *part = &c->parts[c->part_count];
      memset(part, 0, sizeof(*part));
      part->partner = -1;
      if (label(c, row, part->name))
        return -1;
      NEED(c, part_index(c, part->name) < 0, "duplicate partition label");
      part->disk = i;
      part->index = e;
      part->start = r64(row + 32);
      part->end = r64(row + 40);
      NEED(c,
           part->start >= r64(d->template_h[0] + 40) &&
               part->end <= r64(d->template_h[0] + 48) &&
               part->start <= part->end,
           "partition outside usable bounds");
      for (unsigned j = 0; j < c->part_count; j++) {
        const struct part *q = &c->parts[j];
        if (q->disk == i)
          NEED(c, part->end < q->start || part->start > q->end,
               "overlapping profile partitions");
      }
      for (unsigned copy = 0; copy < 2; copy++) {
        uint64_t a = r64(d->template_h[copy] + 72) * 4096,
                 b = a + d->table_bytes;
        NEED(c, b <= part->start * 4096 || a > (part->end * 4096 + 4095),
             "GPT array overlaps a partition");
      }
      uint64_t attrs = r64(row + 48);
      int s = suffix(part->name);
      NEED(c, ((attrs & SLOT_ATTR_ACTIVE) != 0) == (s == 'a'),
           "profile is not coherent source A");
      c->part_count++;
    }
  }
  for (unsigned i = 0; i < c->part_count; i++) {
    struct part *part = &c->parts[i];
    int s = suffix(part->name);
    if (!s)
      continue;
    char other[36];
    strcpy(other, part->name);
    other[strlen(other) - 1] = s == 'a' ? 'b' : 'a';
    part->partner = part_index(c, other);
    NEED(c, part->partner >= 0, "unpaired suffixed partition");
  }
  NEED(c,
       part_index(c, "boot_a") >= 0 && part_index(c, "boot_b") >= 0 &&
           part_index(c, "linux") >= 0 && part_index(c, "misc") >= 0,
       "required partitions absent");
  for (unsigned i = 0; i < c->hash_count; i++) {
    NEED(c, (size_t)(end - p) >= 76, "truncated firmware record");
    struct fw_hash *f = &c->hashes[i];
    memcpy(f->name, p, 36);
    NEED(c, memchr(f->name, 0, 36) != NULL && f->name[0],
         "invalid firmware label");
    for (unsigned j = 0; f->name[j]; j++)
      NEED(c,
           isalnum((unsigned char)f->name[j]) || f->name[j] == '_' ||
               f->name[j] == '-' || f->name[j] == '.',
           "invalid firmware label character");
    f->bytes = r64(p + 36);
    memcpy(f->hash, p + 44, 32);
    p += 76;
    int n = part_index(c, f->name);
    NEED(c, n >= 0, "firmware partition missing");
    const struct part *q = &c->parts[n];
    NEED(c, f->bytes == (q->end - q->start + 1) * 4096,
         "firmware extent mismatch");
    for (unsigned j = 0; j < i; j++)
      NEED(c, strcmp(c->hashes[j].name, f->name), "duplicate firmware hash");
  }
  NEED(c, p == end, "trailing profile data");
  bool have_a = false, have_b = false;
  for (unsigned i = 0; i < c->hash_count; i++) {
    if (!strcmp(c->hashes[i].name, "boot_a"))
      have_a = true;
    if (!strcmp(c->hashes[i].name, "boot_b"))
      have_b = true;
  }
  NEED(c, have_a && have_b, "full boot hashes absent");
  static const char *core[] = {
      "abl",        "aop",        "boot",          "devcfg",      "dtbo",
      "hyp",        "init_boot",  "keymaster",     "modem",       "qupfw",
      "tz",         "vbmeta",     "vbmeta_system", "vendor_boot", "xbl",
      "xbl_config", "xbl_ramdump"};
  for (unsigned i = 0; i < sizeof(core) / sizeof(core[0]); i++)
    for (char s = 'a'; s <= 'b'; s++) {
      char name[36];
      snprintf(name, sizeof(name), "%s_%c", core[i], s);
      bool found = false;
      for (unsigned j = 0; j < c->hash_count; j++)
        if (!strcmp(name, c->hashes[j].name))
          found = true;
      NEED(c, found, "mandatory firmware hash absent: %s", name);
    }
  return 0;
}

static int read_disk_metadata(struct context *c, struct disk *d) {
  for (unsigned copy = 0; copy < 2; copy++) {
    struct copy *m = &d->old[copy];
    uint64_t lba = copy ? d->bytes / 4096 - 1 : 1;
    if (full_read(c, d->fd, m->h, 4096, lba * 4096))
      return -1;
    NEED(c, header_crc(m->h) && header_same_geometry(m->h, d->template_h[copy]),
         "GPT header identity/CRC changed on %s", d->path);
    if (full_read(c, d->fd, m->t, d->table_bytes, r64(m->h + 72) * 4096))
      return -1;
    NEED(c, slot_crc(m->t, d->table_bytes) == r32(m->h + 88),
         "GPT table CRC failed on %s", d->path);
  }
  return 0;
}
static int open_disks(struct context *c) {
  if (c->mock) {
    char marker[PATH_MAX];
    if (pathf(c, marker, sizeof(marker), "%s/.slot-mock-fixture", c->mockdir))
      return -1;
    int fd = open(marker, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    char text[sizeof(MOCK_MARKER)] = {0};
    NEED(c, fd >= 0, "mock marker missing");
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_nlink != 1 ||
        st.st_size != (off_t)strlen(MOCK_MARKER)) {
      close(fd);
      return errorf(c, "mock marker is not an exact regular fixture file");
    }
    ssize_t n = read(fd, text, sizeof(text) - 1);
    close(fd);
    NEED(c,
         n == (ssize_t)strlen(MOCK_MARKER) &&
             !memcmp(text, MOCK_MARKER, (size_t)n),
         "invalid mock marker");
    for (unsigned i = 0; i < 6; i++) {
      struct disk *d = &c->disks[i];
      char id[33];
      hex(d->uuid, 16, id);
      if (pathf(c, d->path, sizeof(d->path), "%s/disk-%s.img", c->mockdir, id))
        return -1;
      d->fd = open(d->path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
      struct stat s;
      NEED(c,
           d->fd >= 0 && !fstat(d->fd, &s) && S_ISREG(s.st_mode) &&
               s.st_nlink == 1 && (uint64_t)s.st_size == d->bytes,
           "invalid mock disk");
      if (read_disk_metadata(c, d))
        return -1;
    }
    return 0;
  }
  DIR *dir = opendir("/sys/class/block");
  NEED(c, dir != NULL, "sysfs block inventory unavailable");
  struct dirent *de;
  int found = 0;
  while ((de = readdir(dir))) {
    const char *n = de->d_name;
    if (n[0] != 's' || n[1] != 'd' || !n[2])
      continue;
    bool letters = true;
    for (unsigned j = 2; n[j]; j++)
      if (n[j] < 'a' || n[j] > 'z')
        letters = false;
    if (!letters)
      continue;
    char path[PATH_MAX];
    if (pathf(c, path, sizeof(path), "/dev/%s", n)) {
      closedir(dir);
      return -1;
    }
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
      continue;
    struct stat st;
    uint64_t size = 0;
    int bs = 0;
    unsigned char h[4096];
    if (fstat(fd, &st) || !S_ISBLK(st.st_mode) ||
        ioctl(fd, BLKGETSIZE64, &size) || ioctl(fd, BLKSSZGET, &bs) ||
        bs != 4096 || full_read(c, fd, h, 4096, 4096)) {
      close(fd);
      continue;
    }
    int which = -1;
    for (unsigned i = 0; i < 6; i++)
      if (!memcmp(h + 56, c->disks[i].uuid, 16))
        which = (int)i;
    if (which < 0) {
      close(fd);
      continue;
    }
    struct disk *d = &c->disks[which];
    if (d->fd >= 0 || size != d->bytes) {
      close(fd);
      closedir(dir);
      return errorf(c, "duplicate/mismatched physical disk");
    }
    d->fd = fd;
    strcpy(d->path, path);
    found++;
  }
  closedir(dir);
  NEED(c, found == 6, "six configured UFS devices were not found");
  for (unsigned i = 0; i < 6; i++)
    if (read_disk_metadata(c, &c->disks[i]))
      return -1;
  return 0;
}
static int determine_source(struct context *c) {
  int ia = part_index(c, "boot_a"), ib = part_index(c, "boot_b");
  char source = 0;
  for (unsigned copy = 0; copy < 2; copy++) {
    uint64_t a = r64(entry(c, &c->parts[ia], copy, false) + 48),
             b = r64(entry(c, &c->parts[ib], copy, false) + 48);
    NEED(c, !!(a & SLOT_ATTR_ACTIVE) != !!(b & SLOT_ATTR_ACTIVE),
         "ambiguous boot ACTIVE flags");
    char s = (a & SLOT_ATTR_ACTIVE) ? 'a' : 'b';
    NEED(c, !source || source == s, "GPT copies disagree on active slot");
    source = s;
    NEED(c,
         (a & SLOT_ATTR_SUCCESS) && (b & SLOT_ATTR_SUCCESS) &&
             !(a & SLOT_ATTR_UNBOOTABLE) && !(b & SLOT_ATTR_UNBOOTABLE),
         "both slots must already be successful and bootable");
  }
  c->source = source;
  c->target = source == 'a' ? 'b' : 'a';
  return 0;
}
static int validate_layout(struct context *c, char source, bool next) {
  for (unsigned di = 0; di < 6; di++) {
    struct disk *d = &c->disks[di];
    for (unsigned i = 0; i < d->count; i++) {
      const unsigned char *canonical = d->canonical + i * 128;
      if (!zero16(canonical))
        continue;
      for (unsigned copy = 0; copy < 2; copy++) {
        const unsigned char *e =
            (next ? d->next[copy].t : d->old[copy].t) + i * 128;
        NEED(c, !memcmp(e, canonical, 128), "unused GPT entry changed");
      }
    }
  }
  for (unsigned i = 0; i < c->part_count; i++) {
    const struct part *p = &c->parts[i];
    const unsigned char *canonical =
        c->disks[p->disk].canonical + p->index * 128;
    const unsigned char *guid = canonical;
    int s = suffix(p->name);
    if (source == 'b' && s) {
      const struct part *q = &c->parts[p->partner];
      guid = c->disks[q->disk].canonical + q->index * 128;
    }
    uint64_t attrs[2];
    bool boot = !strcmp(p->name, "boot_a") || !strcmp(p->name, "boot_b");
    for (unsigned copy = 0; copy < 2; copy++) {
      const unsigned char *e = entry(c, p, copy, next);
      attrs[copy] = r64(e + 48);
      NEED(c,
           !memcmp(e, guid, 16) && !memcmp(e + 16, canonical + 16, 32) &&
               !memcmp(e + 56, canonical + 56, 72),
           "partition layout/type GUID changed: %s", p->name);
      uint64_t allowed = boot ? SLOT_ATTR_AB_MASK : SLOT_ATTR_ACTIVE;
      NEED(c, ((attrs[copy] ^ r64(canonical + 48)) & ~allowed) == 0,
           "unexpected non-slot attributes: %s", p->name);
      NEED(c, !!(attrs[copy] & SLOT_ATTR_ACTIVE) == (s == source),
           "incoherent ACTIVE flag: %s", p->name);
      if (boot)
        NEED(c,
             (attrs[copy] & SLOT_ATTR_SUCCESS) &&
                 !(attrs[copy] & SLOT_ATTR_UNBOOTABLE),
             "slot health changed");
    }
    NEED(c,
         attrs[0] == attrs[1] ||
             (boot && ((attrs[0] ^ attrs[1]) & ~SLOT_ATTR_RETRY) == 0),
         "GPT copies differ beyond boot retries: %s", p->name);
  }
  return 0;
}
static int build_transition(struct context *c) {
  for (unsigned i = 0; i < 6; i++)
    for (unsigned j = 0; j < 2; j++)
      memcpy(&c->disks[i].next[j], &c->disks[i].old[j], sizeof(struct copy));
  for (unsigned i = 0; i < c->part_count; i++) {
    struct part *p = &c->parts[i];
    int s = suffix(p->name);
    for (unsigned copy = 0; copy < 2; copy++) {
      unsigned char *e = entry(c, p, copy, true);
      const unsigned char *old = entry(c, p, copy, false);
      if (s)
        memcpy(e, entry(c, &c->parts[p->partner], copy, false), 16);
      uint64_t a = r64(old + 48), n = s == c->target ? (a | SLOT_ATTR_ACTIVE)
                                                     : (a & ~SLOT_ATTR_ACTIVE);
      if (!strcmp(p->name, "boot_a") || !strcmp(p->name, "boot_b"))
        n = (n & ~SLOT_ATTR_PRIORITY) |
            ((uint64_t)(s == c->target ? 3 : 2) << 48);
      NEED(c, ((n ^ a) & ~(SLOT_ATTR_ACTIVE | SLOT_ATTR_PRIORITY)) == 0,
           "transition changed health/retry bits");
      w64(e + 48, n);
    }
  }
  for (unsigned i = 0; i < 6; i++) {
    struct disk *d = &c->disks[i];
    d->changed = false;
    for (unsigned copy = 0; copy < 2; copy++) {
      header_recalc(d->next[copy].h, d->next[copy].t, d->table_bytes);
      if (memcmp(&d->old[copy], &d->next[copy], sizeof(struct copy)))
        d->changed = true;
    }
  }
  return validate_layout(c, c->target, true);
}
static int hash_firmware(struct context *c) {
  unsigned char *buf = malloc(1024 * 1024);
  NEED(c, buf != NULL, "hash buffer allocation failed");
  for (unsigned i = 0; i < c->hash_count; i++) {
    const struct fw_hash *f = &c->hashes[i];
    const struct part *p = &c->parts[part_index(c, f->name)];
    struct slot_sha256 sha;
    slot_sha_init(&sha);
    uint64_t done = 0;
    while (done < f->bytes) {
      size_t n = f->bytes - done > 1024 * 1024 ? 1024 * 1024
                                               : (size_t)(f->bytes - done);
      if (full_read(c, c->disks[p->disk].fd, buf, n, p->start * 4096 + done)) {
        free(buf);
        return -1;
      }
      slot_sha_update(&sha, buf, n);
      done += n;
    }
    unsigned char digest[32];
    slot_sha_final(&sha, digest);
    if (memcmp(digest, f->hash, 32)) {
      free(buf);
      return errorf(c, "firmware/image hash differs: %s", f->name);
    }
  }
  free(buf);
  return 0;
}
static int clean_filesystem(struct context *c) {
  const struct part *p = &c->parts[part_index(c, "linux")];
  unsigned char sb[1024];
  if (full_read(c, c->disks[p->disk].fd, sb, sizeof(sb),
                p->start * 4096 + 1024))
    return -1;
  NEED(c, sb[56] == 0x53 && sb[57] == 0xef && !memcmp(sb + 104, c->fsuuid, 16),
       "root ext4 UUID/magic differs");
  NEED(c,
       r32(sb + 24) == 2 && (sb[58] & 1) && !(sb[58] & 2) &&
           !(r32(sb + 96) & 4) && r32(sb + 232) == 0,
       "root ext4 is dirty; boot normal Linux first");
  uint64_t blocks = r32(sb + 4) | ((uint64_t)r32(sb + 0x150) << 32);
  NEED(c, blocks > 0 && blocks <= p->end - p->start + 1,
       "root ext4 extent invalid");
  return 0;
}
static int read_misc(struct context *c) {
  const struct part *p = &c->parts[part_index(c, "misc")];
  unsigned char b[32832];
  NEED(c, (p->end - p->start + 1) * 4096 >= sizeof(b),
       "misc partition too short");
  if (full_read(c, c->disks[p->disk].fd, b, sizeof(b), p->start * 4096))
    return -1;
  NEED(c, b[0] == 0 || !memcmp(b, "bootonce-bootloader\0", 20),
       "recovery/unknown boot operation pending");
  const unsigned char *m = b + 32768;
  NEED(c,
       (m[0] == 1 || m[0] == 2) && r32(m + 1) == 0x56740ab0 &&
           (m[5] == 0 || m[5] == 4) && (m[6] == 0 || m[6] == 1),
       "Android OTA snapshot operation pending or unknown");
  return 0;
}

static int small_read(struct context *c, const char *path, unsigned char *out,
                      size_t cap, size_t *len) {
  int fd = open(path, O_RDONLY | O_CLOEXEC);
  NEED(c, fd >= 0, "read %s: %s", path, strerror(errno));
  size_t done = 0;
  while (done < cap) {
    ssize_t n = read(fd, out + done, cap - done);
    if (n < 0 && errno == EINTR)
      continue;
    if (n < 0) {
      close(fd);
      return errorf(c, "read %s failed", path);
    }
    if (!n)
      break;
    done += (size_t)n;
  }
  unsigned char extra;
  ssize_t more = read(fd, &extra, 1);
  close(fd);
  NEED(c, more == 0, "oversized property %s", path);
  *len = done;
  return 0;
}
static int exact_file(struct context *c, const char *path, const void *expected,
                      size_t bytes) {
  unsigned char buf[256];
  size_t n;
  NEED(c, bytes <= sizeof(buf), "internal property bound");
  if (small_read(c, path, buf, sizeof(buf), &n))
    return -1;
  NEED(c, n == bytes && !memcmp(buf, expected, bytes), "unexpected property %s",
       path);
  return 0;
}
static int text_number(struct context *c, const char *path,
                       unsigned long *value) {
  unsigned char b[64];
  size_t n;
  if (small_read(c, path, b, sizeof(b) - 1, &n))
    return -1;
  b[n] = 0;
  char *end;
  errno = 0;
  unsigned long v = strtoul((char *)b, &end, 0);
  NEED(c, !errno && end != (char *)b, "invalid number in %s", path);
  while (*end && isspace((unsigned char)*end))
    end++;
  NEED(c, *end == 0, "trailing data in %s", path);
  *value = v;
  return 0;
}
static int root_guard(struct context *c) {
  if (c->mock)
    return 0;
  NEED(c, geteuid() == 0 && getpid() != 1 && getppid() == 1,
       "slot helper must be an init child");
  NEED(c, !strcmp(c->root, "/newroot"), "unexpected early root mountpoint");
  struct stat st;
  struct statfs fs;
  struct statvfs vfs;
  NEED(c,
       lstat(c->root, &st) == 0 && S_ISDIR(st.st_mode) && st.st_uid == 0 &&
           !S_ISLNK(st.st_mode),
       "insecure early root");
  NEED(c,
       statfs(c->root, &fs) == 0 && fs.f_type == EXT4_SUPER_MAGIC &&
           statvfs(c->root, &vfs) == 0 && (vfs.f_flag & ST_RDONLY),
       "root is not read-only ext4");
  c->root_mounted = true;
  NEED(c, access("/sys/firmware/efi", F_OK) != 0,
       "EFI boot cannot execute native slot action");
  struct utsname u;
  NEED(c, uname(&u) == 0 && !strcmp(u.machine, "aarch64"),
       "unexpected architecture");
  static const char request[] = "other", native[] = "menu3-normal";
  if (exact_file(c, "/proc/device-tree/chosen/sheng,slot-switch", request,
                 sizeof(request)) ||
      exact_file(c, "/proc/device-tree/chosen/sheng,native-boot", native,
                 sizeof(native)))
    return -1;
  unsigned char model[256];
  size_t n;
  if (small_read(c, "/proc/device-tree/model", model, sizeof(model) - 1, &n))
    return -1;
  model[n] = 0;
  NEED(c,
       strstr((char *)model, "Pad 6S Pro") || strstr((char *)model, "Pad6SPro"),
       "unexpected tablet model");
  return 0;
}
static void uuid_text(const unsigned char *u, char out[37]) {
  snprintf(out, 37, "%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x", r32(u),
           u[4] | ((unsigned)u[5] << 8), u[6] | ((unsigned)u[7] << 8), u[8],
           u[9], u[10], u[11], u[12], u[13], u[14], u[15]);
}
static int locate_root_device(struct context *c) {
  if (c->mock) {
    return pathf(c, c->root_device, sizeof(c->root_device), "%s/mock-root",
                 c->mockdir);
  }
  const struct part *p = &c->parts[part_index(c, "linux")];
  const unsigned char *e = c->disks[p->disk].canonical + p->index * 128;
  char uuid[37];
  uuid_text(e + 16, uuid);
  DIR *dir = opendir("/sys/class/block");
  NEED(c, dir != NULL, "block sysfs unavailable");
  struct dirent *de;
  int found = 0;
  while ((de = readdir(dir))) {
    if (de->d_name[0] == '.')
      continue;
    bool safe = true;
    for (const char *q = de->d_name; *q; q++)
      if (!isalnum((unsigned char)*q) && *q != '_' && *q != '-' && *q != '.')
        safe = false;
    if (!safe)
      continue;
    char path[PATH_MAX];
    if (pathf(c, path, sizeof(path), "/sys/class/block/%s/uevent",
              de->d_name)) {
      closedir(dir);
      return -1;
    }
    unsigned char text[2048];
    size_t n;
    if (small_read(c, path, text, sizeof(text) - 1, &n))
      continue;
    text[n] = 0;
    char needle[80];
    snprintf(needle, sizeof(needle), "PARTUUID=%s\n", uuid);
    if (!strstr((char *)text, "PARTNAME=linux\n") ||
        !strstr((char *)text, needle))
      continue;
    unsigned long start, size;
    if (pathf(c, path, sizeof(path), "/sys/class/block/%s/start", de->d_name) ||
        text_number(c, path, &start) ||
        pathf(c, path, sizeof(path), "/sys/class/block/%s/size", de->d_name) ||
        text_number(c, path, &size)) {
      closedir(dir);
      return -1;
    }
    if (start != p->start * 8 || size != (p->end - p->start + 1) * 8) {
      closedir(dir);
      return errorf(c, "Linux kernel partition geometry differs");
    }
    if (pathf(c, c->root_device, sizeof(c->root_device), "/dev/%s",
              de->d_name)) {
      closedir(dir);
      return -1;
    }
    struct stat node, mountpoint;
    if (lstat(c->root_device, &node) || !S_ISBLK(node.st_mode) ||
        stat(c->root, &mountpoint) || node.st_rdev != mountpoint.st_dev) {
      closedir(dir);
      return errorf(c, "early root is not the configured Linux partition");
    }
    found++;
  }
  closedir(dir);
  NEED(c, found == 1, "known Linux block device is not unique");
  return 0;
}
static int bsg_query(struct context *c, bool write, unsigned value,
                     unsigned *result) {
  struct ufs_bsg_request req = {0};
  struct ufs_bsg_reply rsp = {0};
  req.msgcode = 0x16;
  req.upiu_req.header.dword_0 = htobe32(0x16000000);
  req.upiu_req.header.dword_1 = htobe32(write ? 0x00810000 : 0x00010000);
  req.upiu_req.qr.opcode = write ? 4 : 3;
  req.upiu_req.qr.idn = 0;
  req.upiu_req.qr.value = htobe32(value);
  struct sg_io_v4 io = {.guard = 'Q',
                        .protocol = BSG_PROTOCOL_SCSI,
                        .subprotocol = BSG_SUB_PROTOCOL_SCSI_TRANSPORT,
                        .request_len = sizeof(req),
                        .request = (uint64_t)(uintptr_t)&req,
                        .max_response_len = sizeof(rsp),
                        .response = (uint64_t)(uintptr_t)&rsp,
                        .timeout = 5000};
  NEED(c,
       ioctl(c->bsg_fd, 0x2285, &io) == 0 && !io.info && !rsp.result &&
           !io.device_status && !io.transport_status && !io.driver_status,
       "UFS BSG %s failed", write ? "write" : "read");
  NEED(c, (be32toh(rsp.upiu_rsp.header.dword_0) >> 24) == 0x36,
       "unexpected UFS query response");
  if (result)
    *result = be32toh(rsp.upiu_rsp.qr.value);
  return 0;
}
static int get_lun(struct context *c, unsigned *out) {
  if (c->mock) {
    char p[PATH_MAX];
    if (pathf(c, p, sizeof(p), "%s/boot_lun.bin", c->mockdir))
      return -1;
    int fd = open(p, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    NEED(c, fd >= 0, "mock boot LUN missing");
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_nlink != 1 ||
        st.st_size != 4) {
      close(fd);
      return errorf(c, "mock UFS is not a regular four-byte file");
    }
    if (c->mock_lun_stat.st_ino && (st.st_dev != c->mock_lun_stat.st_dev ||
                                    st.st_ino != c->mock_lun_stat.st_ino)) {
      close(fd);
      return errorf(c, "mock UFS file identity changed");
    }
    c->mock_lun_stat = st;
    unsigned char b[4];
    int ret = full_read(c, fd, b, 4, 0);
    close(fd);
    if (ret)
      return ret;
    *out = r32(b);
    NEED(c, *out == 1 || *out == 2, "invalid mock boot LUN");
    return 0;
  }
  unsigned value;
  if (bsg_query(c, false, 0, &value))
    return -1;
  unsigned long sys;
  if (text_number(
          c,
          "/sys/bus/platform/devices/1d84000.ufshc/attributes/boot_lun_enabled",
          &sys))
    return -1;
  NEED(c, (value == 1 || value == 2) && value == sys, "UFS BSG/sysfs disagree");
  *out = value;
  return 0;
}
static int open_bsg(struct context *c) {
  if (c->mock)
    return get_lun(c, &c->old_lun);
  char resolved[PATH_MAX];
  NEED(c,
       realpath("/sys/class/bsg/ufs-bsg0/device", resolved) != NULL &&
           strstr(resolved, "/1d84000.ufshc"),
       "unexpected UFS BSG controller");
  unsigned char text[64];
  size_t n;
  if (small_read(c, "/sys/class/bsg/ufs-bsg0/dev", text, sizeof(text) - 1, &n))
    return -1;
  text[n] = 0;
  unsigned major_n, minor_n;
  char tail;
  NEED(c, sscanf((char *)text, "%u:%u %c", &major_n, &minor_n, &tail) == 2,
       "invalid BSG device number");
  struct stat st;
  if (lstat("/dev/bsg", &st)) {
    NEED(c, errno == ENOENT && mkdir("/dev/bsg", 0755) == 0,
         "cannot create RAM BSG directory");
  } else
    NEED(c, S_ISDIR(st.st_mode) && st.st_uid == 0 && !(st.st_mode & 0022),
         "insecure BSG directory");
  if (lstat("/dev/bsg/ufs-bsg0", &st)) {
    NEED(c,
         errno == ENOENT && mknod("/dev/bsg/ufs-bsg0", S_IFCHR | 0600,
                                  makedev(major_n, minor_n)) == 0,
         "cannot create RAM BSG node");
    NEED(c, lstat("/dev/bsg/ufs-bsg0", &st) == 0, "BSG node unavailable");
  }
  NEED(c, S_ISCHR(st.st_mode) && st.st_rdev == makedev(major_n, minor_n),
       "BSG node identity mismatch");
  c->bsg_fd = open("/dev/bsg/ufs-bsg0", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
  NEED(c, c->bsg_fd >= 0, "open UFS BSG failed");
  return get_lun(c, &c->old_lun);
}
static int find_phandle(struct context *c, const char *dir, uint32_t wanted,
                        char *out, unsigned depth, unsigned *visited) {
  NEED(c, depth <= 32 && ++*visited <= 8192,
       "device tree traversal exceeds bound");
  const char *props[] = {"phandle", "linux,phandle"};
  for (unsigned i = 0; i < 2; i++) {
    char path[PATH_MAX];
    if (pathf(c, path, sizeof(path), "%s/%s", dir, props[i]))
      return -1;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd >= 0) {
      unsigned char b[5];
      ssize_t n = read(fd, b, 5);
      close(fd);
      if (n == 4 && b32(b) == wanted) {
        NEED(c, !out[0] || !strcmp(out, dir), "duplicate nvmem phandle");
        strcpy(out, dir);
      }
    }
  }
  DIR *d = opendir(dir);
  NEED(c, d != NULL, "device tree directory unreadable");
  struct dirent *e;
  while ((e = readdir(d))) {
    if (e->d_name[0] == '.')
      continue;
    char p[PATH_MAX];
    if (pathf(c, p, sizeof(p), "%s/%s", dir, e->d_name)) {
      closedir(d);
      return -1;
    }
    struct stat st;
    if (lstat(p, &st) == 0 && S_ISDIR(st.st_mode))
      if (find_phandle(c, p, wanted, out, depth + 1, visited)) {
        closedir(d);
        return -1;
      }
  }
  closedir(d);
  return 0;
}
static bool string_list_has(const unsigned char *p, size_t n,
                            const char *want) {
  size_t off = 0;
  while (off < n) {
    size_t len = strnlen((const char *)p + off, n - off);
    if (len == n - off)
      return false;
    if (!strcmp((const char *)p + off, want))
      return true;
    off += len + 1;
  }
  return false;
}
static int resolve_nvmem(struct context *c) {
  if (c->mock) {
    if (pathf(c, c->nvmem_path, sizeof(c->nvmem_path), "%s/nvmem.bin",
              c->mockdir))
      return -1;
    c->nvmem_fd =
        open(c->nvmem_path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    NEED(c, c->nvmem_fd >= 0, "mock NVMEM missing");
    NEED(c,
         fstat(c->nvmem_fd, &c->mock_nvmem_stat) == 0 &&
             S_ISREG(c->mock_nvmem_stat.st_mode) &&
             c->mock_nvmem_stat.st_nlink == 1 &&
             c->mock_nvmem_stat.st_size >= 256 &&
             c->mock_nvmem_stat.st_size <= 4096,
         "mock NVMEM is not a bounded regular file");
    return full_read(c, c->nvmem_fd, &c->old_nvmem, 1, NVMEM_OFFSET);
  }
  unsigned char b[256];
  size_t n;
  if (small_read(c, "/proc/device-tree/reboot-mode/compatible", b, sizeof(b),
                 &n))
    return -1;
  NEED(c, string_list_has(b, n, "nvmem-reboot-mode"),
       "unexpected reboot-mode driver");
  if (small_read(c, "/proc/device-tree/reboot-mode/nvmem-cells", b, sizeof(b),
                 &n))
    return -1;
  NEED(c, n == 4 && b32(b) != 0, "unexpected reboot-mode cell list");
  uint32_t ph = b32(b);
  char cell[PATH_MAX] = {0};
  unsigned visited = 0;
  if (find_phandle(c, "/sys/firmware/devicetree/base", ph, cell, 0, &visited))
    return -1;
  NEED(c, cell[0], "reboot NVMEM cell absent");
  char path[PATH_MAX], provider[PATH_MAX], pmic[PATH_MAX];
  if (pathf(c, path, sizeof(path), "%s/reg", cell) ||
      small_read(c, path, b, sizeof(b), &n))
    return -1;
  NEED(c, n == 8 && b32(b) == 0x48 && b32(b + 4) == 1,
       "unexpected reboot cell extent");
  if (pathf(c, path, sizeof(path), "%s/bits", cell) ||
      small_read(c, path, b, sizeof(b), &n))
    return -1;
  NEED(c, n == 8 && b32(b) == 1 && b32(b + 4) == 7,
       "unexpected reboot cell bitfield");
  strcpy(provider, cell);
  char *slash = strrchr(provider, '/');
  NEED(c, slash != NULL, "invalid NVMEM provider");
  *slash = 0;
  if (pathf(c, path, sizeof(path), "%s/reg", provider) ||
      small_read(c, path, b, sizeof(b), &n))
    return -1;
  NEED(c, n == 4 && b32(b) == 0x7100, "unexpected PMIC SDAM provider address");
  if (pathf(c, path, sizeof(path), "%s/compatible", provider) ||
      small_read(c, path, b, sizeof(b), &n))
    return -1;
  NEED(c, string_list_has(b, n, "qcom,spmi-sdam"), "unexpected SDAM driver");
  strcpy(pmic, provider);
  slash = strrchr(pmic, '/');
  NEED(c, slash != NULL, "invalid PMIC parent");
  *slash = 0;
  if (pathf(c, path, sizeof(path), "%s/reg", pmic) ||
      small_read(c, path, b, sizeof(b), &n))
    return -1;
  NEED(c, n >= 4 && b32(b) == 0, "reboot provider is not PMIC SID0");
  if (pathf(c, path, sizeof(path), "%s/compatible", pmic) ||
      small_read(c, path, b, sizeof(b), &n))
    return -1;
  NEED(c, string_list_has(b, n, "qcom,pm8550"), "unexpected PMIC model");
  DIR *dir = opendir("/sys/bus/nvmem/devices");
  NEED(c, dir != NULL, "NVMEM sysfs unavailable");
  struct dirent *e;
  unsigned matches = 0;
  while ((e = readdir(dir))) {
    if (e->d_name[0] == '.')
      continue;
    char link[PATH_MAX], resolved[PATH_MAX];
    if (pathf(c, link, sizeof(link), "/sys/bus/nvmem/devices/%s/of_node",
              e->d_name)) {
      closedir(dir);
      return -1;
    }
    if (realpath(link, resolved) && !strcmp(resolved, provider)) {
      if (pathf(c, c->nvmem_path, sizeof(c->nvmem_path),
                "/sys/bus/nvmem/devices/%s/nvmem", e->d_name)) {
        closedir(dir);
        return -1;
      }
      matches++;
    }
  }
  closedir(dir);
  NEED(c, matches == 1, "reboot NVMEM provider is not unique");
  c->nvmem_fd = open(c->nvmem_path,
                     (c->dry ? O_RDONLY : O_RDWR) | O_CLOEXEC | O_NOFOLLOW);
  NEED(c, c->nvmem_fd >= 0, "open PMIC NVMEM failed");
  return full_read(c, c->nvmem_fd, &c->old_nvmem, 1, NVMEM_OFFSET);
}

static void json_string(FILE *f, const char *s) {
  fputc('"', f);
  for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
    if (*p == '"' || *p == '\\') {
      fputc('\\', f);
      fputc(*p, f);
    } else if (*p < 32)
      fprintf(f, "\\u%04x", *p);
    else
      fputc(*p, f);
  }
  fputc('"', f);
}
static void json_hex(FILE *f, const unsigned char *p, size_t n) {
  static const char a[] = "0123456789abcdef";
  fputc('"', f);
  for (size_t i = 0; i < n; i++) {
    fputc(a[p[i] >> 4], f);
    fputc(a[p[i] & 15], f);
  }
  fputc('"', f);
}
static void report(struct context *c, FILE *f, bool full, const char *status) {
  fprintf(f, "{\n  \"status\":");
  json_string(f, status);
  fprintf(f,
          ",\n  \"source\":\"%c\",\n  \"target\":\"%c\",\n  \"dry_run\":%s,\n  "
          "\"profile_sha256\":",
          c->source ?: '?', c->target ?: '?', c->dry ? "true" : "false");
  json_hex(f, c->profile_sha, 32);
  fprintf(
      f,
      ",\n  \"firmware_hashes_verified\":%u,\n  \"old_boot_lun\":%u,\n  "
      "\"old_reboot_byte\":%u,\n  \"planned_reboot_byte\":%u,\n  \"nvmem\":",
      c->hash_count, c->old_lun, c->old_nvmem, c->old_nvmem & 1);
  json_string(f, c->nvmem_path);
  fprintf(f,
          ",\n  \"metadata_write_calls\":%lu,\n  \"error\":", c->write_count);
  json_string(f, c->error);
  fprintf(f, ",\n  \"disks\":[");
  for (unsigned i = 0; i < 6; i++) {
    struct disk *d = &c->disks[i];
    if (i)
      fputc(',', f);
    fprintf(f, "\n    {\"uuid_bytes\":");
    json_hex(f, d->uuid, 16);
    fprintf(f, ",\"node\":");
    json_string(f, d->path);
    fprintf(f,
            ",\"changed\":%s,\"table_bytes\":%u,\"primary_table_lba\":%llu,"
            "\"backup_table_lba\":%llu",
            d->changed ? "true" : "false", d->table_bytes,
            (unsigned long long)r64(d->old[0].h + 72),
            (unsigned long long)r64(d->old[1].h + 72));
    if (full) {
      fprintf(f, ",\"primary_header\":");
      json_hex(f, d->next[0].h, 4096);
      fprintf(f, ",\"backup_header\":");
      json_hex(f, d->next[1].h, 4096);
      fprintf(f, ",\"primary_table\":");
      json_hex(f, d->next[0].t, d->table_bytes);
      fprintf(f, ",\"backup_table\":");
      json_hex(f, d->next[1].t, d->table_bytes);
    }
    fputc('}', f);
  }
  fprintf(f, "\n  ]\n}\n");
  fflush(f);
}
static int save_report(struct context *c) {
  if (c->mock) {
    if (!c->plan_path[0])
      return 0;
    char realdir[PATH_MAX], parent[PATH_MAX];
    NEED(c, realpath(c->mockdir, realdir), "resolve mock directory failed");
    strcpy(parent, c->plan_path);
    char *slash = strrchr(parent, '/');
    NEED(c, slash && slash[1], "mock plan path must have parent");
    *slash = 0;
    char resolved[PATH_MAX];
    NEED(c, realpath(parent, resolved) && !strcmp(resolved, realdir),
         "mock plan must stay inside mock directory");
    int fd = open(c->plan_path,
                  O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    NEED(c, fd >= 0, "create mock plan failed");
    FILE *f = fdopen(fd, "w");
    NEED(c, f != NULL, "mock plan stream failed");
    report(c, f, true, "planned");
    int ret = fclose(f);
    NEED(c, ret == 0, "write mock plan failed");
    return 0;
  }
  if (mkdir("/run/sheng-native", 0755) && errno != EEXIST)
    return errorf(c, "create RAM report directory failed");
  int fd = open("/run/sheng-native/slot-dry-run.json",
                O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0644);
  NEED(c, fd >= 0, "create RAM dry-run report failed");
  FILE *f = fdopen(fd, "w");
  NEED(c, f != NULL, "RAM report stream failed");
  report(c, f, false, "dry-run-qualified");
  NEED(c, fclose(f) == 0, "RAM report write failed");
  return 0;
}
static int detach_root(struct context *c, bool remount_ro) {
  if (c->mock || !c->root_mounted)
    return 0;
  if (remount_ro && mount(NULL, c->root, NULL, MS_REMOUNT | MS_RDONLY, NULL))
    return errorf(c, "cannot remount early root read-only: %s",
                  strerror(errno));
  NEED(c, umount2(c->root, 0) == 0, "cannot unmount early root: %s",
       strerror(errno));
  c->root_mounted = false;
  return 0;
}
static int owned_directory(struct context *c, const char *path) {
  struct stat st;
  NEED(c,
       lstat(path, &st) == 0 && S_ISDIR(st.st_mode) && !S_ISLNK(st.st_mode) &&
           (c->mock || st.st_uid == 0) && !(st.st_mode & 0022),
       "insecure transaction directory: %s", path);
  return 0;
}
static int data_file(struct context *c, int dir, const char *name,
                     const void *data, size_t bytes) {
  int fd = openat(dir, name, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
                  0600);
  NEED(c, fd >= 0, "create transaction backup failed: %s", name);
  const unsigned char *p = data;
  size_t done = 0;
  while (done < bytes) {
    ssize_t n = write(fd, p + done, bytes - done);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0) {
      close(fd);
      return errorf(c, "write transaction backup failed");
    }
    done += (size_t)n;
  }
  unsigned char *check = malloc(bytes ? bytes : 1);
  if (!check) {
    close(fd);
    return errorf(c, "backup verification allocation failed");
  }
  int ret = fsync(fd);
  if (!ret)
    ret = full_read(c, fd, check, bytes, 0);
  if (!ret && memcmp(check, data, bytes))
    ret = errorf(c, "backup readback failed");
  free(check);
  close(fd);
  NEED(c, ret == 0, "transaction backup durability failed");
  return 0;
}
static int persist_transaction(struct context *c) {
  char base[PATH_MAX];
  if (c->mock) {
    if (pathf(c, base, sizeof(base), "%s/transactions", c->mockdir))
      return -1;
    if (mkdir(base, 0700) && errno != EEXIST)
      return errorf(c, "mock transaction mkdir failed");
  } else {
    if (detach_root(c, false))
      return -1;
    NEED(c, mount(c->root_device, c->root, "ext4", 0, "errors=remount-ro") == 0,
         "cannot mount clean root to save backup: %s", strerror(errno));
    c->root_mounted = true;
    if (pathf(c, base, sizeof(base), "%s/var/lib/sheng-boot-menu", c->root) ||
        owned_directory(c, base))
      return -1;
    char profile[PATH_MAX];
    if (pathf(c, profile, sizeof(profile), "%s/profile.bin", base))
      return -1;
    int fd = open(profile, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    NEED(c, fd >= 0, "reopen profile failed");
    struct stat st;
    unsigned char *blob = malloc(c->blob_bytes);
    if (!blob) {
      close(fd);
      return errorf(c, "profile reread allocation failed");
    }
    int ret = fstat(fd, &st);
    if (!ret &&
        (st.st_size != (off_t)c->blob_bytes || st.st_uid != 0 ||
         (st.st_mode & 0022) || !S_ISREG(st.st_mode) || st.st_nlink != 1))
      ret = -1;
    if (!ret)
      ret = full_read(c, fd, blob, c->blob_bytes, 0);
    close(fd);
    if (!ret && memcmp(blob, c->blob, c->blob_bytes))
      ret = -1;
    free(blob);
    NEED(c, ret == 0, "profile changed before backup persistence");
    if (pathf(c, base, sizeof(base), "%s/var/lib/sheng-boot-menu/transactions",
              c->root))
      return -1;
    if (mkdir(base, 0700) && errno != EEXIST)
      return errorf(c, "transaction parent mkdir failed");
  }
  if (owned_directory(c, base))
    return -1;
  struct timespec ts;
  NEED(c, clock_gettime(CLOCK_MONOTONIC, &ts) == 0, "monotonic clock failed");
  if (pathf(c, c->transaction, sizeof(c->transaction),
            "%s/slot-%c-to-%c-%llu-%ld-%ld", base, c->source, c->target,
            (unsigned long long)ts.tv_sec, ts.tv_nsec, (long)getpid()))
    return -1;
  NEED(c, mkdir(c->transaction, 0700) == 0,
       "transaction already exists or cannot be created");
  int dir =
      open(c->transaction, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  NEED(c, dir >= 0, "open transaction failed");
  int ret = data_file(c, dir, "profile.bin", c->blob, c->blob_bytes);
  for (unsigned i = 0; !ret && i < 6; i++) {
    struct disk *d = &c->disks[i];
    char id[33], name[80];
    hex(d->uuid, 16, id);
    for (unsigned copy = 0; !ret && copy < 2; copy++) {
      snprintf(name, sizeof(name), "%s-%s-header.bin", id,
               copy ? "backup" : "primary");
      ret = data_file(c, dir, name, d->old[copy].h, 4096);
      if (!ret) {
        snprintf(name, sizeof(name), "%s-%s-table.bin", id,
                 copy ? "backup" : "primary");
        ret = data_file(c, dir, name, d->old[copy].t, d->table_bytes);
      }
    }
  }
  if (!ret) {
    char manifest[1024], sha[65];
    hex(c->profile_sha, 32, sha);
    int n = snprintf(
        manifest, sizeof(manifest),
        "{\n \"format\":2,\n \"state\":\"prepared-before-any-GPT-write\",\n "
        "\"source\":\"%c\",\n \"target\":\"%c\",\n \"source_boot_lun\":%u,\n "
        "\"old_reboot_byte\":%u,\n \"profile_sha256\":\"%s\",\n "
        "\"copies\":\"headers-and-count-times-128-byte-arrays; UUID "
        "filenames\"\n}\n",
        c->source, c->target, c->old_lun, c->old_nvmem, sha);
    if (n < 0 || (size_t)n >= sizeof(manifest))
      ret = errorf(c, "manifest exceeds bound");
    else
      ret = data_file(c, dir, "manifest.json", manifest, (size_t)n);
  }
  if (!ret && fsync(dir))
    ret = errorf(c, "transaction directory fsync failed");
  if (!ret && syncfs(dir))
    ret = errorf(c, "transaction filesystem sync failed");
  close(dir);
  int parent = open(base, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (parent < 0)
    return errorf(c, "transaction parent reopen failed");
  if (fsync(parent) && !ret)
    ret = errorf(c, "parent directory fsync failed");
  close(parent);
  if (ret)
    return ret;
  if (!c->mock) {
    if (detach_root(c, true) || clean_filesystem(c))
      return -1;
  }
  fprintf(stderr, "slot-helper: source metadata durably saved at %s\n",
          c->transaction);
  return 0;
}
static int snapshot_matches(struct context *c, struct disk *d, bool next) {
  unsigned char *buf = malloc(COPY_BYTES);
  NEED(c, buf != NULL, "metadata verification allocation failed");
  for (unsigned copy = 0; copy < 2; copy++) {
    const struct copy *m = next ? &d->next[copy] : &d->old[copy];
    uint64_t lba = copy ? d->bytes / 4096 - 1 : 1;
    if (full_read(c, d->fd, buf, 4096, lba * 4096) || memcmp(buf, m->h, 4096) ||
        !header_crc(buf)) {
      free(buf);
      return errorf(c, "GPT header readback differs on %s", d->path);
    }
    if (full_read(c, d->fd, buf, d->table_bytes, r64(m->h + 72) * 4096) ||
        memcmp(buf, m->t, d->table_bytes) ||
        slot_crc(buf, d->table_bytes) != r32(m->h + 88)) {
      free(buf);
      return errorf(c, "GPT table readback differs on %s", d->path);
    }
  }
  free(buf);
  return 0;
}
static int open_write_disks(struct context *c) {
  NEED(c, !c->dry, "dry run cannot open writable disks");
  for (unsigned i = 0; i < 6; i++) {
    struct disk *d = &c->disks[i];
    struct stat old, now;
    NEED(c, fstat(d->fd, &old) == 0, "existing disk stat failed");
    int fd = open(d->path, O_RDWR | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    NEED(c, fd >= 0, "open writable disk failed: %s", d->path);
    if (fstat(fd, &now) || now.st_mode != old.st_mode ||
        (c->mock ? (now.st_dev != old.st_dev || now.st_ino != old.st_ino ||
                    !S_ISREG(now.st_mode))
                 : (now.st_rdev != old.st_rdev || !S_ISBLK(now.st_mode)))) {
      close(fd);
      return errorf(c, "disk node changed before write");
    }
    close(d->fd);
    d->fd = fd;
    if (snapshot_matches(c, d, false))
      return -1;
  }
  if (c->mock) {
    close(c->nvmem_fd);
    c->nvmem_fd =
        open(c->nvmem_path, O_RDWR | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    struct stat st;
    NEED(c,
         c->nvmem_fd >= 0 && fstat(c->nvmem_fd, &st) == 0 &&
             S_ISREG(st.st_mode) && st.st_nlink == 1 &&
             st.st_dev == c->mock_nvmem_stat.st_dev &&
             st.st_ino == c->mock_nvmem_stat.st_ino &&
             st.st_size == c->mock_nvmem_stat.st_size,
         "mock NVMEM writable identity changed");
  }
  return 0;
}
static int write_checked_io(struct context *c, int fd, const void *data,
                            size_t bytes, uint64_t offset, bool sync_fd) {
  NEED(c, c->started && !c->dry,
       "write attempted outside explicit transaction");
  c->write_count++;
#ifdef SLOT_TEST_BUILD
  if (c->fault_at > 0 &&
      ((long)c->write_count == c->fault_at ||
       (c->persistent_fault && (long)c->write_count >= c->fault_at))) {
    size_t n = (bytes + 1) / 2;
    if (n && pwrite(fd, data, n, (off_t)offset) < 0)
      return errorf(c, "mock partial write syscall failed");
    (void)fsync(fd);
    return errorf(c, "injected partial metadata write failure at %lu",
                  c->write_count);
  }
#endif
  const unsigned char *p = data;
  size_t done = 0;
  while (done < bytes) {
    ssize_t n = pwrite(fd, p + done, bytes - done, (off_t)(offset + done));
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0)
      return errorf(c, "metadata write failed at %llu",
                    (unsigned long long)(offset + done));
    done += (size_t)n;
  }
  NEED(c, !sync_fd || fsync(fd) == 0, "metadata fsync failed");
  unsigned char *check = malloc(bytes ? bytes : 1);
  NEED(c, check != NULL, "write verification allocation failed");
  int ret = full_read(c, fd, check, bytes, offset);
  if (!ret && memcmp(check, data, bytes))
    ret = errorf(c, "metadata byte readback failed");
  free(check);
  return ret;
}
static int write_checked(struct context *c, int fd, const void *data,
                         size_t bytes, uint64_t offset) {
  return write_checked_io(c, fd, data, bytes, offset, true);
}
static int write_copy(struct context *c, struct disk *d, unsigned copy,
                      bool next) {
  const struct copy *m = next ? &d->next[copy] : &d->old[copy];
  uint64_t header_lba = copy ? d->bytes / 4096 - 1 : 1;
  if (write_checked(c, d->fd, m->t, d->table_bytes, r64(m->h + 72) * 4096) ||
      write_checked(c, d->fd, m->h, 4096, header_lba * 4096))
    return -1;
  unsigned char h[4096], t[ARRAY_BYTES];
  if (full_read(c, d->fd, h, 4096, header_lba * 4096) ||
      full_read(c, d->fd, t, d->table_bytes, r64(m->h + 72) * 4096))
    return -1;
  NEED(c,
       header_crc(h) && !memcmp(h, m->h, 4096) &&
           !memcmp(t, m->t, d->table_bytes) &&
           slot_crc(t, d->table_bytes) == r32(h + 88),
       "complete GPT copy verification failed");
  return 0;
}
static int set_lun(struct context *c, unsigned value) {
  NEED(c, c->started && !c->dry && (value == 1 || value == 2),
       "UFS write outside transaction");
  if (c->mock) {
    char path[PATH_MAX];
    if (pathf(c, path, sizeof(path), "%s/boot_lun.bin", c->mockdir))
      return -1;
    int fd = open(path, O_RDWR | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    NEED(c, fd >= 0, "mock UFS writable open failed");
    struct stat st;
    NEED(c,
         fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_size == 4 &&
             st.st_nlink == 1 && st.st_dev == c->mock_lun_stat.st_dev &&
             st.st_ino == c->mock_lun_stat.st_ino,
         "invalid mock UFS file");
    unsigned char b[4];
    w32(b, value);
    int ret = write_checked(c, fd, b, 4, 0);
    close(fd);
    if (ret)
      return -1;
  } else if (bsg_query(c, true, value, NULL))
    return -1;
  unsigned actual;
  if (get_lun(c, &actual))
    return -1;
  NEED(c, actual == value, "UFS boot LUN write did not persist");
  return 0;
}
static int write_nvmem(struct context *c, unsigned char value) {
  NEED(c, c->started && !c->dry, "PMIC write outside transaction");
  c->nvmem_attempted = true;
  /* sysfs NVMEM writes are synchronous hardware operations; fsync on a
   * sysfs attribute is unsupported (EINVAL), so verify the byte directly. */
  if (write_checked_io(c, c->nvmem_fd, &value, 1, NVMEM_OFFSET, c->mock))
    return -1;
  unsigned char actual;
  if (full_read(c, c->nvmem_fd, &actual, 1, NVMEM_OFFSET))
    return -1;
  NEED(c, actual == value, "PMIC reboot byte readback failed");
  return 0;
}
static int verify_all(struct context *c, bool next) {
  for (unsigned i = 0; i < 6; i++)
    if (snapshot_matches(c, &c->disks[i], next))
      return -1;
  return hash_firmware(c);
}
static int rollback(struct context *c) {
  stage(c, "Restoring source boot metadata after a failed switch...", true);
  bool ok = true;
  /* Restore every touched disk, continuing after an error to recover as much
   * as possible. Each copy is header-directed and verified independently. */
  for (unsigned i = 0; i < 6; i++)
    if (c->disks[i].touched) {
      if (write_copy(c, &c->disks[i], 1, false))
        ok = false;
      if (write_copy(c, &c->disks[i], 0, false))
        ok = false;
    }
  if (set_lun(c, c->old_lun))
    ok = false;
  if (c->nvmem_attempted && write_nvmem(c, c->old_nvmem))
    ok = false;
  if (verify_all(c, false))
    ok = false;
  unsigned lun;
  if (get_lun(c, &lun) || lun != c->old_lun)
    ok = false;
  if (c->nvmem_attempted) {
    unsigned char value;
    if (full_read(c, c->nvmem_fd, &value, 1, NVMEM_OFFSET) ||
        value != c->old_nvmem)
      ok = false;
  }
  return ok ? 0 : -1;
}
static int apply_transaction(struct context *c) {
  char message[64];
  snprintf(message, sizeof(message), "Switching to slot %c...",
           toupper(c->target));
  stage(c, message, true);
  if (persist_transaction(c) || open_write_disks(c))
    return SLOT_EXIT_REFUSED;
  /* Started is set before pwrite, including a partial/failed first write. */
  c->started = true;
  for (unsigned i = 0; i < 6; i++)
    if (c->disks[i].changed) {
      struct disk *d = &c->disks[i];
      d->touched = true;
      if (write_copy(c, d, 1, true) || write_copy(c, d, 0, true))
        goto failed;
    }
  if (verify_all(c, true))
    goto failed;
  stage(c, "Target GPT copies verified; selecting UFS boot LUN.", false);
  if (set_lun(c, c->target == 'a' ? 1 : 2))
    goto failed;
  unsigned char old;
  if (full_read(c, c->nvmem_fd, &old, 1, NVMEM_OFFSET) || old != c->old_nvmem) {
    errorf(c, "PMIC byte changed since preflight");
    goto failed;
  }
  if (write_nvmem(c, c->old_nvmem & 1))
    goto failed;
  stage(c, "GPT, UFS and normal reboot reason verified; restarting.", false);
  if (!c->mock) {
    sync();
    reboot(RB_AUTOBOOT);
    errorf(c, "normal kernel reboot returned: %s", strerror(errno));
    goto failed;
  }
  report(c, stdout, false, "mock-committed");
  return 0;
failed:;
  char original[sizeof(c->error)];
  strcpy(original, c->error);
  if (rollback(c)) {
    char rollback_error[sizeof(c->error)];
    strcpy(rollback_error, c->error);
    errorf(c, "transaction failed (%s); rollback uncertain (%s)", original,
           rollback_error);
    return SLOT_EXIT_UNSAFE;
  }
  errorf(c,
         "transaction failed; complete source GPT/UFS/PMIC state restored: %s",
         original);
  return SLOT_EXIT_ROLLED_BACK;
}

static void cleanup(struct context *c) {
  for (unsigned i = 0; i < 6; i++)
    if (c->disks[i].fd >= 0)
      close(c->disks[i].fd);
  if (c->bsg_fd >= 0)
    close(c->bsg_fd);
  if (c->nvmem_fd >= 0)
    close(c->nvmem_fd);
  free(c->blob);
  free(c);
}
static int crypto_selftest(struct context *c) {
  static const unsigned char expected[32] = {
      0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40,
      0xde, 0x5d, 0xae, 0x22, 0x23, 0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17,
      0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad};
  unsigned char h[32];
  slot_sha("abc", 3, h);
  NEED(c, !memcmp(h, expected, 32) && slot_crc("123456789", 9) == 0xcbf43926,
       "hash self-test failed");
  return 0;
}
#ifndef SLOT_NO_MAIN
int main(int argc, char **argv) {
  setvbuf(stdout, NULL, _IONBF, 0);
  struct context *c = calloc(1, sizeof(*c));
  if (!c)
    return SLOT_EXIT_REFUSED;
  c->dry = true;
  c->bsg_fd = c->nvmem_fd = -1;
  for (unsigned i = 0; i < 6; i++)
    c->disks[i].fd = -1;
  strcpy(c->root, "/newroot");
  bool explicit_switch = false, transform_only = false;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--dry-run"))
      c->dry = true;
    else if (!strcmp(argv[i], "--switch-other")) {
      c->dry = false;
      explicit_switch = true;
    } else if (!strcmp(argv[i], "--root") && i + 1 < argc) {
      if (strlen(argv[++i]) >= sizeof(c->root))
        goto usage;
      strcpy(c->root, argv[i]);
    } else if (!strcmp(argv[i], "--mock") && i + 1 < argc) {
      c->mock = true;
      if (strlen(argv[++i]) >= sizeof(c->mockdir))
        goto usage;
      strcpy(c->mockdir, argv[i]);
    } else if (!strcmp(argv[i], "--profile") && i + 1 < argc) {
      if (strlen(argv[++i]) >= sizeof(c->profile_path))
        goto usage;
      strcpy(c->profile_path, argv[i]);
    } else if (!strcmp(argv[i], "--plan") && i + 1 < argc) {
      if (strlen(argv[++i]) >= sizeof(c->plan_path))
        goto usage;
      strcpy(c->plan_path, argv[i]);
    }
#ifdef SLOT_TEST_BUILD
    else if (!strcmp(argv[i], "--apply-mock")) {
      c->test_apply = true;
      c->dry = false;
    } else if (!strcmp(argv[i], "--test-transform")) {
      transform_only = true;
    } else if (!strcmp(argv[i], "--fault-at") && i + 1 < argc) {
      char *end;
      c->fault_at = strtol(argv[++i], &end, 10);
      if (*end || c->fault_at < 1)
        goto usage;
    } else if (!strcmp(argv[i], "--persistent-fault"))
      c->persistent_fault = true;
#endif
    else
      goto usage;
  }
  if (c->mock) {
    if (!c->profile_path[0] || explicit_switch)
      goto usage;
    c->dry = !c->test_apply;
  } else if (c->profile_path[0] || c->plan_path[0] || c->test_apply ||
             transform_only)
    goto usage;
  int ret = SLOT_EXIT_REFUSED;
  if (crypto_selftest(c) || root_guard(c) || checked_profile_open(c) ||
      parse_profile(c) || open_disks(c) || determine_source(c) ||
      validate_layout(c, c->source, false) || build_transition(c))
    goto done;
  if (transform_only) {
    if (save_report(c))
      goto done;
    report(c, stdout, false, "test-transform-only");
    ret = 0;
    goto done;
  }
  stage(c, "Verifying boot slots...", true);
  if (clean_filesystem(c) || locate_root_device(c) || read_misc(c) ||
      hash_firmware(c) || open_bsg(c) || resolve_nvmem(c))
    goto done;
  if (c->old_lun != (unsigned)(c->source == 'a' ? 1 : 2)) {
    errorf(c, "source GPT and UFS boot LUN disagree");
    goto done;
  }
  if (c->dry) {
    c->error[0] = 0;
    if (save_report(c))
      goto done;
    report(c, stdout, false, "dry-run-qualified");
    goto done;
  }
  if (!c->mock && !explicit_switch) {
    errorf(c, "write mode requires --switch-other");
    goto done;
  }
  ret = apply_transaction(c);
done:
  if (c->root_mounted && detach_root(c, !c->dry))
    ret = SLOT_EXIT_UNSAFE;
  if (ret) {
    fprintf(stderr, "slot-helper: %s (exit %d)\n",
            c->error[0] ? c->error : "dry run finished without changes", ret);
  }
  cleanup(c);
  return ret;
usage:
  fprintf(stderr,
          "Usage: slot-helper --dry-run|--switch-other --root /newroot\nMock "
          "dry-run: --mock DIR --profile FILE [--plan DIR/plan.json]\n");
  cleanup(c);
  return SLOT_EXIT_REFUSED;
}
#endif
