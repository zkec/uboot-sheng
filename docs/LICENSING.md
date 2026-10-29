# Licensing scope

The repository's default license for its independent tooling, tests and documentation is **GNU GPL version 3 only**, SPDX `GPL-3.0-only`. The unmodified license text is in [LICENSE](../LICENSE).

This is a mixed-license boot project. A root license does not replace another component's permissions or turn a complete firmware image into a GPLv3-only program.

| Files/component | License |
| --- | --- |
| `tools/`, `tests/`, root Makefile, project documentation | GPL-3.0-only |
| `advanced/slot-switch/build_legacy_bridge.py` | GPL-3.0-only; independent host tool |
| `src/`, `configs/`, `patches/`, `assets/menu-art.h`, `assets/menu-render.h` | GPL-2.0-or-later, preserving boot-runtime compatibility |
| `advanced/slot-switch/init-branch.template.sh` | GPL-2.0-or-later for integration into a qualified initramfs |
| Slot helper/hash/profile sources and their original tests/Makefile | MIT; retain [their notice](../advanced/slot-switch/LICENSE) |
| `vendor/libfdt/` | Original GPL-2.0-or-later OR BSD-2-Clause, with upstream notices retained |
| Third-party icons and artwork | Their original licenses and attribution; see [NOTICE](../NOTICE) |

The full GPLv2 text needed by the runtime exception is retained at [LICENSES/GPL-2.0.txt](../LICENSES/GPL-2.0.txt). It does not replace the root GPLv3 license for independent project programs. The standalone host preview can use GPLv3 with the renderer's later-version permission.

## Why the boot-runtime exception is necessary

The pinned U-Boot checkout contains GPLv2-only files. In the validated build, `cmd/button.c` is labeled `GPL-2.0`, and its `cmd/button.o` contributes code to `u-boot`. Qualcomm headers also carry GPLv2-only notices. The `-or-later` permission on many other U-Boot files does not override those restrictions.

GPLv2-only and GPLv3-only code cannot simply be combined into one distributable program. Keeping the U-Boot integration, entry shim and generated runtime declarations GPLv2-compatible avoids forcing the assembled boot programs to GPLv3. Separate build tools and documentation can use GPLv3 without changing the licenses of the programs they process.

This package therefore makes **no claim that the whole U-Boot binary or complete Android/Linux boot image is GPLv3-only**. Changing that would require an audit and appropriate permission or replacement for incompatible upstream code; editing a license label alone is insufficient.

Primary references:

- [U-Boot's licensing notes at the pinned revision](https://github.com/u-boot/u-boot/blob/5508406582f6f7120dce0c4b3059819a41788db2/Licenses/README)
- [The pinned button-command source](https://github.com/u-boot/u-boot/blob/5508406582f6f7120dce0c4b3059819a41788db2/cmd/button.c)
- [GNU FAQ: GPLv2/GPLv3 compatibility](https://www.gnu.org/licenses/gpl-faq.en.html#v2v3Compatibility)
- [GNU GPLv3 text](https://www.gnu.org/licenses/gpl-3.0.html)

Retain all existing copyright and third-party license notices when redistributing. Previously distributed copies retain their original license grants.
