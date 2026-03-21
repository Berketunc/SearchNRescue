# IoT Mesh Dashboard — Setup Guide

## 1. Prerequisites
- Python 3.10 or newer (`python --version`)
- VSCode with the **Python** extension installed
- Your Pico 2 connected via USB, printing JSON to serial

---

## 2. Create a Virtual Environment in VSCode

### Option A — VSCode Command Palette (recommended)
1. Open your project folder in VSCode (`File → Open Folder`)
2. Press `Ctrl+Shift+P` → **Python: Create Environment**
3. Choose **Venv** → select your Python 3.10+ interpreter
4. VSCode creates `.venv/` and activates it automatically in the integrated terminal

### Option B — Integrated Terminal
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

After activation your terminal prompt will show `(.venv)`.

---

## 3. Install Dependencies
```bash
pip install -r requirements.txt
```

---

## 4. Select the Interpreter in VSCode
Press `Ctrl+Shift+P` → **Python: Select Interpreter** → pick `.venv` from the list.  
This ensures VSCode's linter, IntelliSense, and Run button all use your venv.

---

## 5. Run the Dashboard
```bash
python dashboard.py
```
NiceGUI opens a browser tab automatically at **http://localhost:8080**.  
If it doesn't, navigate there manually.

---

## 6. Using the Dashboard

| Control | Action |
|---|---|
| Port dropdown | Select your Pico's COM port (e.g. `COM3`, `/dev/ttyACM0`) |
| **↻** button | Refresh the port list |
| **CONNECT** | Opens the serial port |
| **DISCONNECT** | Closes it cleanly |
| **▶ LOG CSV** | Starts appending all readings to `sensor_log.csv` |
| **■ STOP LOG** | Stops logging |

Sensor cards appear **automatically** for every new `node_id` seen.

---

## 7. Finding Your Pico's Port

**Windows** — Device Manager → Ports (COM & LPT)  
**macOS** — `ls /dev/cu.*`  
**Linux** — `ls /dev/ttyACM*` or `ls /dev/ttyUSB*`

---

## 8. Pico 2 Output Format
The dashboard expects newline-terminated JSON on the serial port:
```json
{"id": 1, "temp": 24.2, "humidity": 45, "rssi": -55}
```
Fields `id`, `temp`, `humidity`, `rssi` are all recognised. Any extra fields are silently ignored. Malformed lines are skipped without crashing.

---

## 9. CSV Log Format
```
timestamp,id,temp,humidity,rssi
2025-03-21T14:32:01,1,24.2,45,-55
```
The file is appended to on every run (not overwritten).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Port busy / permission denied" | Close MicroPython REPL, Thonny, PuTTY, or any other tool using the port |
| No ports in dropdown | Click **↻** after plugging in the Pico |
| Cards never appear | Check Pico is printing valid JSON with an `"id"` field |
| `ModuleNotFoundError` | Make sure venv is activated and `pip install -r requirements.txt` succeeded |