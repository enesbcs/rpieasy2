#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd 2>/dev/null || echo .)"
echo "==> APT Update"
sudo apt-get update -qq

if [ ! -f "${SCRIPT_DIR}/main.py" ]; then
    echo "==> main.py not found. Downloading project from GitHub..."
    sudo apt-get install -y -qq unzip
    TEMP_ZIP="/tmp/rpieasy2-$$.zip"
    curl -fsSL -o "${TEMP_ZIP}" "https://github.com/enesbcs/rpieasy2/archive/refs/heads/main.zip"
    mkdir -p "${HOME}/rpieasy2"
    TEMP_DIR="/tmp/rpieasy2-extract-$$"
    mkdir -p "${TEMP_DIR}"
    unzip -q -o "${TEMP_ZIP}" -d "${TEMP_DIR}"
    cp -r "${TEMP_DIR}/rpieasy2-main/"* "${HOME}/rpieasy2/"
    rm -rf "${TEMP_DIR}" "${TEMP_ZIP}"
    echo "==> Extracted to ${HOME}/rpieasy2, running install.sh from there..."
    cd "${HOME}/rpieasy2"
    exec bash install.sh
fi

DEST="${HOME}/rpieasy2"
SRC="${SCRIPT_DIR}"
SERVICE_NAME="rpieasy2"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "==> RPiEasy2 install to ${DEST}"

if [ "$(id -u)" -eq 0 ]; then
    echo "WARNING: Running as root is not the normal installation mode."
    read -p "Are you sure you want to run the service as root? (Y/N): " choice
    case "$choice" in
        [Yy]) echo "==> Continuing installation as root..." ;;
        *) echo "Aborting."; exit 1 ;;
    esac
fi

echo "==> Installing system dependencies (python3, python3-venv)..."
sudo apt-get install -y -qq python3 python3-venv

IS_RPI=false
if [ -f /proc/device-tree/model ] && grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
    IS_RPI=true
elif [ -f /sys/firmware/devicetree/base/model ] && grep -qi "raspberry" /sys/firmware/devicetree/base/model 2>/dev/null; then
    IS_RPI=true
fi
sudo usermod -aG dialout $(whoami)
if [ "$IS_RPI" = true ]; then
    echo "==> Raspberry Pi detected, installing liblgpio-dev..."
    sudo apt-get install -y -qq liblgpio-dev
    sudo usermod -aG gpio $(whoami)
    sudo usermod -aG i2c $(whoami)
    if ! grep -q "^i2c-dev$" /etc/modules 2>/dev/null; then
        echo "==> Adding i2c-dev to /etc/modules..."
        echo "i2c-dev" | sudo tee -a /etc/modules > /dev/null
    fi
fi

IS_ALTERNATIVE_BOARD=false
if [ "$IS_RPI" = false ] && [ -f /etc/os-release ]; then
    if grep -qi "^PRETTY_NAME=.*armbian" /etc/os-release 2>/dev/null; then
        if [ -f /proc/device-tree/model ] && grep -qiE "radxa|orange" /proc/device-tree/model 2>/dev/null; then
            IS_ALTERNATIVE_BOARD=true
            echo "==> Radxa/Orange Pi detected (Armbian), installing gpiod..."
            sudo apt-get install -y -qq gpiod python3-dev libgpiod-dev build-essential
            if getent group gpio > /dev/null 2>&1; then
                echo "  - gpio group already exists"
            else
                echo "==> Creating gpio group..."
                sudo groupadd gpio
            fi
            sudo usermod -aG gpio "$USER"
            if getent group i2c > /dev/null 2>&1; then
                echo "  - i2c group already exists"
            else
                echo "==> Creating i2c group..."
                sudo groupadd i2c
            fi
            sudo usermod -aG i2c "$USER"
            if [ ! -f /etc/udev/rules.d/99-i2c.rules ]; then
                echo 'SUBSYSTEM=="i2c-dev", KERNEL=="i2c-[0-9]*", GROUP="i2c", MODE="0660"' | sudo tee /etc/udev/rules.d/99-i2c.rules > /dev/null
                sudo udevadm control --reload-rules
                sudo udevadm trigger
            fi
            if [ ! -f /etc/udev/rules.d/99-gpiochip.rules ]; then
                echo 'SUBSYSTEM=="gpio", KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"' | sudo tee /etc/udev/rules.d/99-gpiochip.rules > /dev/null
                sudo udevadm control --reload-rules
                sudo udevadm trigger
            fi
            if ! grep -q "^i2c-dev$" /etc/modules 2>/dev/null; then
                echo "==> Adding i2c-dev to /etc/modules..."
                echo "i2c-dev" | sudo tee -a /etc/modules > /dev/null
            fi
        fi
    fi
fi

mkdir -p "${DEST}/config"

if [ "${SRC}" = "${DEST}" ]; then
    echo "==> SRC and DEST are the same, skipping file copy..."
else
    echo "==> Copying files..."
    cp "${SRC}/main.py" "${DEST}/"
    cp "${SRC}/requirements.txt" "${DEST}/"
    cp -r "${SRC}/rpieasy2" "${DEST}/rpieasy2"
    if [ -f "${SRC}/config/rpieasy2.json" ]; then
        cp "${SRC}/config/rpieasy2.json" "${DEST}/config/"
    elif [ -f "${SRC}/rpieasy2.json" ]; then
        cp "${SRC}/rpieasy2.json" "${DEST}/config/"
        echo "  (migrated config to config/ subdirectory)"
    fi
    find "${DEST}" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
fi

echo "==> Creating virtual environment..."
python3 -m venv "${DEST}/venv"

echo "==> Installing core dependencies..."
"${DEST}/venv/bin/pip" install --upgrade pip --quiet
"${DEST}/venv/bin/pip" install -r "${DEST}/requirements.txt"

echo "==> Installing optional hardware backends..."
if [ "$IS_RPI" = true ]; then
    echo "  (Raspberry Pi detected: installing native GPIO/I2C/SPI backends)"
    for pkg in lgpio smbus2 spidev; do
        if "${DEST}/venv/bin/pip" install --quiet "${pkg}" 2>/dev/null; then
            echo "  - ${pkg}: installed"
        else
            echo "  - ${pkg}: not available (optional, skipping)"
        fi
    done
    if ! "${DEST}/venv/bin/python3" -c "import lgpio" 2>/dev/null || \
       ! "${DEST}/venv/bin/python3" -c "import spidev" 2>/dev/null; then
        echo "  -> lgpio/spidev not found in venv, installing system packages..."
        sudo apt-get install -y -qq python3-lgpio python3-smbus2 python3-spidev
        python3 -m venv --system-site-packages "${DEST}/venv"
    fi
    echo "  (rpi-ws281x: skipped - install manually from pluginlist if needed)"
elif [ "$IS_ALTERNATIVE_BOARD" = true ]; then
    echo "  (Radxa/Orange Pi detected: installing gpiod/smbus2/spidev backends)"
    for pkg in gpiod smbus2 spidev; do
        if "${DEST}/venv/bin/pip" install --quiet "${pkg}" 2>/dev/null; then
            echo "  - ${pkg}: installed"
        else
            echo "  - ${pkg}: not available (optional, skipping)"
        fi
    done
else
    echo "  (Non-RPi platform: installing FTDI backend)"
    if "${DEST}/venv/bin/pip" install --quiet pyftdi 2>/dev/null; then
        echo "  - pyftdi: installed"
    else
        echo "  - pyftdi: not available (optional, skipping)"
    fi
fi

echo "==> Creating systemd service..."
sudo tee "${SERVICE_FILE}" > /dev/null << SERVICE
[Unit]
Description=RPiEasy2
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${DEST}
ExecStart=${DEST}/venv/bin/python ${DEST}/main.py
Restart=on-failure
RestartSec=1

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo "==> Creating run.sh..."
cat > "${DEST}/run.sh" << 'RUNEOF'
#!/usr/bin/env bash
set -euo pipefail
SVC="rpieasy2"
case "${1:-}" in
    stop)
        sudo systemctl stop "${SVC}"
        echo "RPiEasy2 stopped"
        ;;
    status)
        sudo systemctl status "${SVC}"
        ;;
    log)
        sudo journalctl -u "${SVC}" -n 50 -f
        ;;
    install|reinstall)
        cd "$(dirname "$0")"
        exec "${0%/*}/install.sh"
        ;;
    help|--help|-h)
        echo "Usage: $(basename "$0") [command]"
        echo "  start    Start (or restart) the rpieasy2 service (default)"
        echo "  stop     Stop the service"
        echo "  status   Show service status"
        echo "  log      Tail the service log"
        echo "  help     Show this help"
        ;;
    *)
        sudo systemctl restart "${SVC}"
        echo "RPiEasy2 service restarted"
        ;;
esac
RUNEOF
chmod +x "${DEST}/run.sh"

echo "==> Done!"
echo "    Service: ${SERVICE_NAME} (started and enabled)"
echo "    Manage with: ${DEST}/run.sh [start|stop|status|log]"
echo "    Or: sudo systemctl <command> ${SERVICE_NAME}"

if [ -f /proc/device-tree/model ] && grep -qi "radxa" /proc/device-tree/model 2>/dev/null; then
    echo "==> Radxa board detected, running rockoverlay.sh..."
    bash "${SCRIPT_DIR}/rpieasy2/static/rockoverlay.sh"
fi
