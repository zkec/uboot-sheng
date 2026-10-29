# Troubleshooting

| Symptom | What to check |
| --- | --- |
| `fastboot devices` is empty or permission is denied | Cable, USB mode and the Linux computer's Android udev permissions |
| `current-slot` says B | Stop. This menu is built for A's matching Android companion path. It is not a generic B boot image |
| Capture rejects GPT CRCs, mismatched copies or ACTIVE flags | Existing metadata is outside the supported baseline. Do not edit JSON or use old slot tools to bypass it |
| Capture rejects a pending snapshot/merge/recovery request | Return through the existing working Android/recovery route and resolve it there before a fresh capture |
| Builder says Android is already a selector | Restore the previous setup's matching unwrapped Android image first; another wrapped menu is not a stock source |
| Extractor rejects the native boot image | It accepts native header-v0 Linux with a gzip Image and appended DTB, not Android v4 or an EDK2/UEFI firmware image |
| Candidate immediately starts Android | A wrapper guard may have chosen safe Android fallback. Check A, matching companions, supported memory layout and supplied DTB. Do not install |
| Menu works but Linux stops | Check the supplied native command line/root, matching modules and that the same original Linux image already works. The initramfs is preserved |
| Linux lacks sound, sleep, Wi-Fi or keyboard support | These are kernel/userspace requirements of the Linux installation; this repository does not bundle the reference tablet's desktop fixes |
| Output directory already exists | Choose a new `--output .build/menu-v2`, then verify and transfer that exact directory. Keep captures |
| Installer says firmware differs | A capture is stale or the current device is different. Do not replace expected hashes by hand |
| Installer reports a rollback failure | Keep the running Linux session alive. Use the verified operation backup and a qualified recovery path; do not assume a restart will fix it |

For an issue, include the codename, RAM/storage variant, Android/HyperOS version, Linux release, failing step and the exact error. Say whether this was a RAM test or an installed boot.

Do **not** attach captures, GPT dumps, full boot images, SSH credentials or a root filesystem. Redact serial numbers, IP addresses and personal paths from logs. The build/QA report's pass/fail cases and selected non-personal fields are usually sufficient to start a discussion.
