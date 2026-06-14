#!/bin/bash
set -e

if [ ! -f /boot/armbianEnv.txt ]; then
    echo "/boot/armbianEnv.txt not found. Exiting."
    exit 0
fi

prefix=$(grep -s '^overlay_prefix=' /boot/armbianEnv.txt | head -1 | cut -d= -f2 | tr -d ' ')
if [ "$prefix" != "rk3308" ]; then
    echo "overlay_prefix is '$prefix', expected 'rk3308'. Exiting."
    exit 0
fi

check_files=("rk3308-i2c1.dtbo")
all_found=true
for f in "${check_files[@]}"; do
    if ! find /boot -name "$f" 2>/dev/null | grep -q .; then
        all_found=false
        break
    fi
done

if $all_found; then
    echo "All overlay dtbo files already exist under /boot/. Nothing to do."
    exit 0
fi

dts_urls=(
    "https://raw.githubusercontent.com/radxa/kernel/stable-4.4-rockpis/arch/arm64/boot/dts/rockchip/overlay/rk3308-i2c1.dts"
    "https://raw.githubusercontent.com/radxa/kernel/refs/heads/stable-4.4-rockpis/arch/arm64/boot/dts/rockchip/overlay/rk3308-w1-gpio.dts"
    "https://raw.githubusercontent.com/radxa/kernel/refs/heads/stable-4.4-rockpis/arch/arm64/boot/dts/rockchip/overlay/rk3308-spi-spidev.dts"
)

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

cd "$tmpdir"

for url in "${dts_urls[@]}"; do
    fname=$(basename "$url")
    echo "Downloading $fname ..."
    curl -sSLO "$url" || { echo "Failed to download $url"; exit 1; }
done

for dts in *.dts; do
    echo "Adding overlay: $dts"
    sudo armbian-add-overlay "$dts"
done

echo "Done."
