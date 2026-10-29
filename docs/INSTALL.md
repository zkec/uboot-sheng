# Installation

Read [requirements](REQUIREMENTS.md) first. Keep important data backed up. The process below starts with **both operating systems already working** and A already bootable.
Commands marked **computer** run on the Linux build computer. Commands marked **tablet** run in the tablet's existing native Debian/Linux terminal. `sudo` asks for the password in that terminal.

## 1. Get the source and your own Linux input

**Computer:** clone this repository.

```sh
git clone https://github.com/zkec/uboot-sheng.git
cd uboot-sheng
mkdir -p private
```

Copy your **known-working native Linux boot image** to `private/linux-native.img`. Use the image from your own Linux installation.

Install the computer dependencies and create `.venv` as shown in [requirements](REQUIREMENTS.md).

## 2. Enter native Linux temporarily while A stays active

Go into bootloader from Your Android, check if its on slot a:

**Computer:**

```sh
fastboot getvar current-slot
```

Continue only when it reports **`a`**. If it reports B, stop and resolve that through your existing qualified setup. Do not run `fastboot set_active` or `qbootctl` as a substitute.

Boot your original native Linux image from RAM:

```sh
fastboot boot private/linux-native.img
```

Log in on the tablet. This is your real Linux filesystem, not a disposable overlay. Temporary boot means the boot image was loaded from RAM; files you create in Linux can still persist.

## 3. Capture this tablet's own recovery data

**Tablet:**

Clone the same repository into `~/uboot-sheng`, then:

```sh
git clone https://github.com/zkec/uboot-sheng.git ~/uboot-sheng
cd ~/uboot-sheng
sudo python3 tools/capture_device.py --output private/capture
sudo chown -R "$(id -u):$(id -g)" private/capture
```

The collector reads the current firmware, both GPT copies on all six UFS disks, and the real Linux filesystem UUID. It saves complete boot A/B backups and checks that they match a second inventory.

If any guard fails, **do not continue by editing the captured JSON**. The files describe this physical tablet and its current firmware.

**Computer:**

 set the SSH destination to your Linux username and tablet IP. `192.168.x.x` below is only an example address; replace it:

```sh
export TABLET='your_linux_user@192.168.x.x'
scp -r "$TABLET:~/uboot-sheng/private/capture" private/
```

## 4. Build your candidate

**Computer:**

```sh
python3 tools/extract_linux.py --boot private/linux-native.img --output private/linux
```

The command copies your native initramfs unchanged and extracts its kernel, DTB and original Linux command line. Read `private/linux/cmdline.txt`: it must describe your own working installation. The project does not supply a root UUID.

Build the pinned U-Boot and package the menu:

```sh
python3 tools/build_uboot.py --jobs 4
python3 tools/build_menu.py --capture private/capture --linux private/linux
.venv/bin/python tools/verify_arm64.py --directory .build/menu
```

Expected final check: **`PASS 15 actual ARM64 wrapper cases.`** These checks execute the wrapper's machine code and test successful handoffs and guarded Android fallback.

The candidate is `.build/menu/selector.img`. The associated `report.json` records your input/output hashes. Do not rename or mix reports from another build. No boot image has been written yet.

## 5. Test Android and Linux from RAM

**Tablet:**

save work and return to fastboot:

```sh
sudo systemctl reboot --reboot-argument=bootloader
```

**Computer:**

```sh
fastboot getvar current-slot
fastboot boot .build/menu/selector.img
```

Again, require A. Acknowledge the unlocked/AVB prompt if your bootloader presents one. Use Volume Up/Down and a brief Power press.

First choose **Android**. Confirm normal startup and your existing root setup, if any. Then return to fastboot and confirm your boot slot and boot the image again:

```sh
fastboot getvar current-slot
fastboot boot .build/menu/selector.img
```

Choose **Linux** and log in. Check display, touch, Wi-Fi, keyboard, brightness, sound, storage and swap. Check suspend only if it is already reliable with your native kernel.

If either choice fails, stop here. The stored boot images are still the originals. Use the existing bootloader/recovery route; do not flash the candidate to try to fix a failed RAM boot.

## 6. Install the same tested bytes on A

Leave the tablet in the successful **native Linux RAM test**, connected to Wi-Fi.

**Computer:**

:copy only the final candidate files:

```sh
ssh "$TABLET" 'mkdir -p ~/uboot-sheng/private/candidate'
scp .build/menu/selector.img .build/menu/report.json .build/menu/qa.json \
  "$TABLET:~/uboot-sheng/private/candidate/"
```

**Tablet:**

first run the check without writing:

```sh
cd ~/uboot-sheng
sudo python3 tools/install.py --capture private/capture --candidate private/candidate
```

If it passes and **both RAM boots worked**, install:

```sh
sudo python3 tools/install.py --capture private/capture --candidate private/candidate --install
```

The installer verifies the exact capture, current firmware, A's successful state and all GPT copies. It saves another full pre-write A backup, writes only the candidate prefix, reads the whole A partition back, and checks B and all firmware/metadata again. A reported error triggers a verified A rollback. Power interruption is not a transaction that software can always roll back.

It never calls a slot switch, resets success/retry flags, zero-fills the remaining boot partition, or installs an updater. Operation records are in `private/capture/operations/`.

Only after the installer reports success:

```sh
sudo systemctl reboot
```

Test the installed Android choice and the installed direct-Linux choice. Keep the private capture and [restore procedure](RECOVERY.md) available.
