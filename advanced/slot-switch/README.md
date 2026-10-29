# Advanced: qualified slot switching

This is the source of the reference device's native-initramfs slot helper. It is **not enabled by the standard public menu build** and the public installer refuses an advanced image. Copying the model name, someone else's profile or a known-working menu binary does not qualify a different tablet's metadata/firmware.

The helper itself is included intact under MIT. Build and run its **synthetic file tests**:

```sh
make -C advanced/slot-switch
make test-slot
```

`bin/slot-helper` is a static ARM64 executable. Production mock mode is read-only. Writable mocks and failure injection are compiled only in test binaries.

## Why this is separate

The reference tablet's old `qbootctl -s` transition produced incoherent type GUIDs/ACTIVE flags. Its ABL also used a fixed-size GPT writer with an unsafe location relative to this tablet's actual GPT arrays. A second tablet or firmware may have different behavior. Generic `fastboot set_active`/success-bit resets are not a substitute for qualification.

The working reference transition was independently run through that tablet's **two actual ABL versions**, with negative controls, before physical tests. Proprietary ABL files and personal GPT captures are not included here. The synthetic tests prove the helper's transaction behavior; they do not recreate another firmware's bootloader or prove power-loss atomicity.

## Required integration

An experienced porter must qualify all of these on their own tablet:

1. Six disks, both valid GPT copies, every typed A/B pair, both images already successful/bootable, and coherent flags/GUIDs for the source.
2. The target's real ABL behavior, including whether it will rewrite GPT or UFS attributes after this transition.
3. Both directions' full firmware/image hashes and the actual UFS controller/query interface.
4. The reboot NVMEM provider, SID/address/cell/bit schema. This implementation requires PM8550 SID0 SDAM cell `0x48`, bits `<1 7>`; RTC `0xbc` must stay untouched.
5. An initramfs branch invoked only by `/chosen/sheng,slot-switch = "other"`, before fsck/services, with the real clean root mounted `ro,noload` at `/newroot`.
6. The exact static helper and required native marker, source firmware, kernel modules, backup persistence and recovery paths.
7. Read-only hardware dry runs before real writes; physical A→B→A tests; a proven route back from the preserved B image.

There is no public command here that supplies missing qualification or silently rewrites a profile to bypass failure.

`init-branch.template.sh` shows the early-init integration contract. It is a porting template, not a standalone script; it requires a verified root device, module loader, `/newroot` and a read-only diagnostics path from the owning initramfs. It defaults to dry-run.

The profile generator needs your own UUID-keyed GPT capture, your full stored partition hashes, and an explicitly supplied filesystem UUID:

```sh
python3 advanced/slot-switch/generate_profile.py --help
```

It has no personal/default filesystem UUID. `boot_a` must describe the **full stored partition** after the candidate prefix plus the preserved old tail, not just the candidate `.img` file. `profile.bin` is private root-owned data at `/var/lib/sheng-boot-menu/profile.bin`; it is not a public device-independent asset.

After a porter has separately qualified a suitable helper initramfs, `tools/build_uboot.py --experimental-slot-switch` exposes the sixth button. The menu builder checks for that helper and explicit init branch, but this syntactic check is **not** firmware qualification. The normal end-user installer remains blocked for such builds.

## Helper behavior

Only an early native init child can perform real writes. Ordinary runtime execution and EFI boots are refused. Dry-run returns 10 and saves a RAM report; it does not write GPT, UFS or NVMEM.

The real path backs up both GPT copies and the profile on the clean root, fsyncs, remounts read-only and unmounts before metadata writes. It swaps all paired type GUIDs and normalizes ACTIVE bits while preserving SUCCESS/retry/unbootable flags; target/source priorities become 3/2. Entry arrays use the locations declared by each header, backup first, with CRC and byte readback. After complete verification it sets and verifies UFS boot LUN, clears reboot-reason bits while retaining bit 0, then reboots normally.

Return 20 means exact source rollback was fully verified. Return 30 means rollback is uncertain; init must stop with storage read-only. Unexpected exits must also stop, never continue into a normal writable session.

Prepared backups are kept conservatively at roughly 200 KiB per switch on the reference layout. There is no automatic deletion or background service. A multi-LUN transition is not atomic across a power interruption.

Preserving B's old UEFI recovery also preserves its old screen. The reference B→A test used a separate self-contained header-v0 native RAM bridge over USB because the A-derived v4 menu did not start with B's companion files. Do not promise a menu or PC-free return from an unchanged B image.

`build_legacy_bridge.py --help` is the corresponding source-only v0 packer. It accepts the porter's own kernel, DTB, qualified helper initramfs and native command-line file, defaults to dry-run, and writes files on the host only. It preserves the supplied initramfs. Before **each** physical RAM boot, separately require `fastboot getvar current-slot` to report B. Do not build or run a real-switch bridge until the dry bridge and all qualification steps above pass.
