# RPiEasy2

> Built with [OpenCode](https://opencode.ai) CLI.

A user-configurable multi-sensor and actuator system for **Raspberry Pi** and other Linux devices — inspired by [ESPEasy](https://github.com/letscontrolit/ESPEasy).

Like ESPEasy, RPiEasy2 lets you connect various sensors and actuators, configure them through a built-in web interface, and send the data to any home automation controller of your choice. The difference is that RPiEasy2 runs on a full Linux system (Raspberry Pi OS, Ubuntu, etc.) instead of an ESP8266/ESP32 microcontroller.

## Features

- **50+ sensor/actuator plugins** — DHT, BME280, BH1750, HC-SR04, Dallas DS18B20, OLED displays, PIR, relays, and many more
- **Controller support** — Domoticz, Home Assistant (MQTT), ThingSpeak, EmonCMS, FHEM, Zabbix, generic HTTP/UDP, and P2P
- **Built-in web UI** — add/remove sensors, view live values, manage rules
- **Rules engine** — trigger actions based on sensor values, timers, or system events
- **Notifications** — email, buzzer
- **FTDI support** — use USB-to-GPIO adapters on non-RPi platforms

## Supported hardware

- Raspberry Pi (GPIO, I²C, SPI, 1-Wire) — natively supported
- Any Linux PC with FTDI FT232H/FT2232H adapters
- All major sensor families (temperature, humidity, light, distance, gas, motion, current/voltage, RFID, etc.)

## Installation

```bash
git clone <repo-url> rpieasy2
cd rpieasy2
chmod +x install.sh
./install.sh
```
OR

```
bash -c "$(curl -fsSL https://raw.githubusercontent.com/enesbcs/rpieasy2/main/install.sh)"
```

The installer will:
1. Install system dependencies (Python 3, venv)
2. Detect if running on a Raspberry Pi and install GPIO backends accordingly
3. Create a Python virtual environment and install dependencies
4. Copy files to `~/rpieasy2`
5. Create and start a **systemd service** (`rpieasy2`)
6. Create a convenience `run.sh` for managing the service

### Manual start (without systemd)

```bash
python3 main.py --host 0.0.0.0 --port 8080
```

Open `http://<your-device-ip>:8080` in a browser to access the web UI.

## Requirements

- Python 3.8+
- See `requirements.txt` for Python dependencies
- Optional hardware backends: `lgpio`, `smbus2`, `spidev` (RPi); `pyftdi` (FTDI)

## Original project

RPiEasy is a port/concept inspired by [ESPEasy](https://github.com/letscontrolit/ESPEasy), adapted for the Raspberry Pi platform.
