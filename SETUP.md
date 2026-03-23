# SearchNRescue Setup Guide

This guide prepares both sides of the project:

- Desktop dashboard environment (Windows focused)
- MicroPython firmware deployment to Scout and Anchor Pico boards

## 1. Requirements

### Hardware

- 2x Raspberry Pi Pico/Pico W (one Scout, one Anchor)
- 2x nRF24L01 modules
- HC-SR04 ultrasonic sensor (Scout)
- BMI160 IMU (Scout)
- Jumper wires and stable 3.3V for nRF24L01

### Software

- Python 3.10+
- VS Code with Python extension
- mpremote installed in your desktop Python environment

## 2. Desktop Python Environment

From project root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install PyQt6 pyserial mpremote
```

Optional verification:

```powershell
python -c "import PyQt6, serial; print('ok')"
python -m mpremote --help
```

## 3. Flash MicroPython to Pico Boards

Install latest MicroPython UF2 on both Pico boards before copying project files.

## 4. Deploy Anchor Firmware (Anchor Pico)

Connect Anchor Pico via USB and run:

```powershell
python -m mpremote connect auto fs cp nrf24l01.py :nrf24l01.py
python -m mpremote connect auto fs cp Anchor/Anchor.py :main.py
python -m mpremote connect auto reset
```

Anchor should print telemetry messages in serial after Scout starts transmitting.

## 5. Deploy Scout Firmware (Scout Pico)

Connect Scout Pico via USB and run:

```powershell
python -m mpremote connect auto fs cp nrf24l01.py :nrf24l01.py
python -m mpremote connect auto fs mkdir control
python -m mpremote connect auto fs cp control/imu.py :control/imu.py
python -m mpremote connect auto fs cp Scout/scout.py :main.py
python -m mpremote connect auto reset
```

If your Scout firmware imports additional control modules later, copy them into :control/ as needed.

## 6. Wiring Reference

### nRF24L01 (both nodes)

- SCK -> GP18
- MOSI -> GP19
- MISO -> GP16
- CSN -> GP14
- CE -> GP17
- VCC -> 3.3V
- GND -> GND

### Scout Sensors

- HC-SR04 candidates in code:
  - TRIG GP6, ECHO GP7 (default)
  - fallback mappings also tried in code
- BMI160 I2C:
  - SDA GP4
  - SCL GP5

## 7. Run Dashboard

From project root:

```powershell
py -3 dashboard/dashboard.py
```

Or explicit interpreter:

```powershell
c:/Users/berke/SearchNRescue/.venv/Scripts/python.exe dashboard/dashboard.py
```

In dashboard:

1. Select Anchor COM port
2. Click CONNECT
3. Verify serial log updates with distance/gyro lines

## 8. Troubleshooting

### Python was not found

Use:

```powershell
py -3 dashboard/dashboard.py
```

or explicit .venv path.

### mpremote not found

Install into active environment:

```powershell
pip install mpremote
```

### Serial PermissionError / Access is denied

Another app is holding the COM port.
Close MicroPico vREPL, Thonny, Arduino Serial Monitor, PuTTY, or any serial monitor using that port.

### No telemetry in dashboard

- Confirm Scout and Anchor are powered and running
- Confirm both nRF24L01 modules share wiring and 3.3V
- Confirm dashboard is connected to Anchor USB serial port
- Check Anchor serial output first with a serial monitor

## 9. Validation Checklist

- Desktop imports pass: PyQt6 + pyserial + mpremote
- Anchor prints telemetry over USB serial
- Dashboard shows changing radar dots and attitude values
- COM port can connect/disconnect cleanly without busy-port errors
