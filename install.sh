#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd 2>/dev/null || echo .)"

if [ ! -f "${SCRIPT_DIR}/main.py" ]; then
    echo "==> main.py not found. Downloading project from GitHub..."
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
    echo "ERROR: Do not run this script as root. It uses sudo where needed."
    exit 1
fi

echo "==> Installing system dependencies (python3, python3-venv)..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv

IS_RPI=false
if [ -f /proc/device-tree/model ] && grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
    IS_RPI=true
elif [ -f /sys/firmware/devicetree/base/model ] && grep -qi "raspberry" /sys/firmware/devicetree/base/model 2>/dev/null; then
    IS_RPI=true
fi
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
else
    echo "  (Non-RPi platform: installing FTDI backend)"
    if "${DEST}/venv/bin/pip" install --quiet pyftdi 2>/dev/null; then
        echo "  - pyftdi: installed"
    else
        echo "  - pyftdi: not available (optional, skipping)"
    fi
fi
echo "  (rpi-ws281x: skipped - install manually from pluginlist if needed)"

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
