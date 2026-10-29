# Restore and recovery

## Failed RAM test

`fastboot boot` loads an image into RAM. The project build tools do not write a tablet, and the default menu has no storage driver or slot-switch action. Restart using the device's existing bootloader/recovery route. The original stored images remain available.

Do not respond to a black screen by erasing partitions, relocking the bootloader or flashing random images. Use your own known-working image and verify the current slot.

## Return A to its captured original image

From the tablet's native Linux with **A active**, and with the capture/candidate still present:

```sh
cd ~/uboot-sheng
sudo python3 tools/install.py --capture private/capture --candidate private/candidate --restore
```

Restore requires the exact currently installed candidate and unchanged firmware/GPTs. It backs up current A, restores the original complete A image, verifies readback and B/metadata, then exits without rebooting. If that reports success, restart normally.

If normal menu boot is unavailable but Xiaomi fastboot works, boot **your own original native Linux image from RAM while A is already active**, then run the same command. Do not activate another slot just to satisfy a guard.

An OTA, another kernel installer, or manual partition changes can make an old capture inapplicable. Refusal is deliberate; a backup from the same tablet at another firmware revision is not automatically the correct target now.

## What stays unchanged

The normal installer preserves boot B and all GPT bytes. Its restore path never writes GPT, UFS boot-LUN settings, AVB metadata or PMIC cells. This project is not a general repair tool for existing GPT damage.

The optional experimental slot helper is different: it writes boot-control metadata. Its recovery requirements are described separately in [advanced/slot-switch](../advanced/slot-switch/README.md). Preserving an old B recovery image also preserves its old interface; it does not magically acquire the new menu.
