# Development and publishing

## Source and build

`tools/build_uboot.py` checks out official U-Boot `v2026.10` and verifies its exact commit. It applies `patches/0001-sheng-platform.patch`, copies `src/sheng_menu` and the renderer, and builds using `configs/sheng.config` and `configs/sheng.env`. It never edits a separate existing U-Boot checkout.

`tools/build_menu.py` creates a FIT containing the owner's native kernel/initramfs, embeds it with U-Boot and the control DTB after the original Android kernel, and links the ARM64 wrapper. Dynamic relocation or unsafe RAM extents are rejected. The user's Linux command line is a separate DT property; it is not compiled into the public source.

On Android selection, `sheng_android.c` checks and restores the original kernel before returning to its original entry with the original Android DTB. On Linux selection, the environment reads the owner's native command line, removes the temporary framebuffer and boots the FIT directly.

The power command uses PSCI. Bootloader restart resolves the mainline NVMEM reboot cell before setting the specific bootloader request; it leaves the RTC cell alone. See `src/sheng_menu/sheng-reboot-policy.h`.

## Tests

```sh
make test
make test-slot
```

The ordinary tests exercise bounded parsers, bad GPTs/slot states, prefix/tail preservation, rollback on partial writes, and source-release exclusions. The advanced tests use **synthetic six-disk files**, not anyone's partition dump. They test both slot directions and partial GPT/UFS/PMIC failures.

After every real candidate build:

```sh
.venv/bin/python tools/verify_arm64.py --directory .build/menu
```

This executes 15 actual ARM64 wrapper cases in Unicorn. Only a complete pass updates `report.json` with `wrapper_qa_passed`. Hardware tests remain manual and must use that same image. Do not describe synthetic or emulator tests as tests on other tablets.

Build output directories must be new. When rebuilding, choose `--output .build/menu-v2` and use that same path for verification and transfer; keep the original private captures untouched.

## Artwork

The current generated `assets/menu-art.h` is already included. Changing colors/layout uses `assets/menu-render.h`; menu command order lives in `src/sheng_menu/sheng_menu.c`.

To regenerate labels/icons on the computer:

```sh
sudo apt install python3-pil librsvg2-bin
python3 tools/flatten_tux.py
python3 tools/generate_artwork.py --font /path/to/your/font.ttf
```

The supplied masks used Inter. A different font changes label appearance. Keep the attribution in `NOTICE` and `assets/ARTWORK.txt`. No font, stock Android image, Linux kernel/module binary or rootfs belongs in the source ZIP.
