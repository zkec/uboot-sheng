# SPDX-License-Identifier: GPL-2.0-or-later
# PORTING TEMPLATE ONLY. Not a standalone script or a complete /init.
# Place after the known Linux UUID/device and all required modules are verified,
# before fsck/services. The parent init must implement a storage-read-only
# diagnostics function named fallback. $device is the verified ext4 block node.
# Keep --dry-run until both firmware and hardware qualification have passed.
# SHENG-SELF-CONTAINED-SLOT-BEGIN
slot_halt()
{
    echo "Slot action stopped: $*" > /dev/console
    # A porter's diagnostics handler must not allow normal startup afterward.
    # Even if it is missing or returns, this local halt never falls through.
    if command -v fallback >/dev/null 2>&1; then fallback "$*"; fi
    while :; do sleep 60; done
}
if [ -e /proc/device-tree/chosen/sheng,slot-switch ]; then
    [ "$(tr -d '\000' < /proc/device-tree/chosen/sheng,slot-switch)" = other ] || slot_halt 'Unknown slot request.'
    slot_features=$(od -An -tu4 -j 1120 -N 4 "$device" | tr -d ' \n')
    slot_state=$(od -An -tu2 -j 1082 -N 2 "$device" | tr -d ' \n')
    slot_clean=no
    case "$slot_features:$slot_state" in
        *[!0-9:]*|:*|*:) ;;
        *) if [ "$((slot_features & 4))" -eq 0 ] && [ "$slot_state" -eq 1 ]; then slot_clean=yes; fi ;;
    esac
    if [ "$slot_clean" = yes ] && mount -t ext4 -o ro,noload "$device" /newroot; then
        slot_status=0
        /slot-helper --dry-run --root /newroot || slot_status=$?
        if grep -qs ' /newroot ' /proc/mounts; then
            umount /newroot || slot_halt 'Cannot unmount slot profile root.'
        fi
        grep -qs ' /newroot ' /proc/mounts && slot_halt 'Slot profile root remains mounted.'
        case "$slot_status" in
            10|20) echo "Slot action returned safely ($slot_status); continuing Linux." ;;
            *) slot_halt "Unsafe or unexpected slot status $slot_status." ;;
        esac
    else
        echo 'Slot action refused an unclean/unavailable root; continuing Linux.'
    fi
fi
# SHENG-SELF-CONTAINED-SLOT-END
