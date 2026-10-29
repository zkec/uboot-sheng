# Tested behavior and limits

## Reference hardware versus this public package

The development implementation was tested on one Pad 6S Pro: Android and direct Linux, Volume/Power navigation, Restart, Bootloader, Power off.
Those are reference-device results, not proof for every firmware. The public package removes personal captures, changes the Linux command line to a per-owner input, and defaults slot switching off. Its clean-source build and automated host checks are separate from hardware qualification. Test your own exact candidate from RAM.

## Android and TrustZone

The shim retains the original Android kernel and bootloader-provided DTB/initrd. Before Android handoff it restores the modified ARM64 header words in RAM and verifies the original kernel hash. It does not replace XBL, ABL, TrustZone, keymaster, modem, or Android companion partitions.

This is not a promise that every device retains a particular DRM level. Bootloader unlocking, root, firmware and service policy affect attestation and streaming independently. Root/DRM behavior must be checked on the actual device.

## Linux

The existing kernel, DTB and initramfs must already support the hardware. The project preserves your initramfs; it does not distribute the reference device's display, fingerprint/sleep. The control tree adds the menu framebuffer, passes the owner's native command line, and excludes Hall/tablet-mode input nodes. Linux boot removes the temporary framebuffer node before kernel handoff.

Existing sound, suspend, charger-program, animation or driver problems remain Linux-side work.
