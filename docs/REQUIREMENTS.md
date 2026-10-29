# Requirements

## Tablet

1. Already unlocked bootloader
2. Working Android on **A**
3. Working native ARM64 Linux and an ext4 partition named **`linux`**
4. A reliable USB cable and stable power

The normal build does not require a physical keyboard or rooted Android. Android USB debugging is convenient for rebooting; the menu itself uses the tablet's buttons.

If a previous menu already wraps Android A, restore that setup's matching original Android image through its own qualified restore procedure first. A wrapped menu backup is not an unwrapped Android input.

If A is not already selected, or the capture reports inconsistent GPTs, stop. This project intentionally supplies no generic slot-repair recipe. In particular, do not substitute old `qbootctl -s` or copied GPT backups from another tablet.

## Linux build computer

Use a Linux computer with Python **3.10+**, roughly **4 GB free disk space**, and enough RAM to build U-Boot and run the ARM64 emulator. Cross compilation supports an x86-64 host; an ARM64 Linux host can also use the listed cross compiler.

Debian package names:

```sh
sudo apt update
sudo apt install git build-essential bc bison flex \
  libssl-dev libgnutls28-dev python3 python3-venv python3-dev \
  python3-pyelftools swig gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
  clang lld llvm device-tree-compiler u-boot-tools xxd adb fastboot

python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
```

Run those from the repository on the **computer**. No compiler or emulator is installed on the tablet by this project. The tablet-side scripts use Python's standard library plus the existing `findmnt` utility.

Check that USB access works before starting:

```sh
fastboot devices
fastboot getvar product
```

The product should identify `sheng`. Configure your distribution's Android udev permissions if your user cannot access the device. Never unlock or relock the bootloader as part of these menu instructions.

Optional artwork regeneration additionally uses Pillow, `rsvg-convert` (`librsvg2-bin`) and a font file. Pre-generated artwork is supplied, so these are not build requirements.

Building the optional static slot helper also needs ARM64 static libc headers/libraries (`libc6-dev-arm64-cross`). Its host tests need the system UFS BSG headers, normally supplied by `linux-libc-dev` with the C development packages.

## Format limits

- Android source: unwrapped boot header **v3/v4**, supported ARM64 branch header, no embedded boot ramdisk. Companion `init_boot`, `vendor_boot`, `dtbo`, `vbmeta`, etc. must still match the capture.
- Linux source: native ARM64 Image (raw/gzip), Sheng DTB, and a working gzip/uncompressed `newc` initramfs. The extraction command supports native v0 images with 4096-byte pages and an appended DTB.
- The kernel and combined payload must fit the guarded RAM windows. The builder rejects oversized/overlapping layouts instead of guessing new addresses.
- Arbitrary Android firmware, other RAM/storage variants, and other kernels are not universally qualified by the reference test.
