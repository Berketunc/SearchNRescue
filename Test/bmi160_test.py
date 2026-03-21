"""
BMI160 IMU Test - MicroPython for Raspberry Pi Pico 2
======================================================
Sensor:  Bosch BMI160 (3-axis Accel + 3-axis Gyro)
Board:   Raspberry Pi Pico 2 (RP2350)
Interface: I2C

Wiring (default pins - change I2C_SDA / I2C_SCL if needed):
  BMI160 VCC  --> 3.3V  (pin 36)
  BMI160 GND  --> GND   (pin 38)
  BMI160 SDA  --> GP4   (pin 6)
  BMI160 SCL  --> GP5   (pin 7)
  BMI160 SA0  --> GND   (I2C address = 0x68)
               or 3.3V  (I2C address = 0x69)

Output every 0.5 s:
  - Raw accelerometer (X, Y, Z) in m/s²
  - Raw gyroscope    (X, Y, Z) in °/s
  - Pitch & Roll     (degrees, calculated from accel)
"""

import time
import math
import struct
from machine import I2C, Pin

# ── User config ────────────────────────────────────────────────────────────────
I2C_ID  = 0          # I2C bus number  (0 or 1)
I2C_SDA = 4          # GP4
I2C_SCL = 5          # GP5
I2C_FREQ = 400_000   # 400 kHz fast mode

BMI160_ADDR = 0x68   # SA0 → GND = 0x68 | SA0 → 3.3V = 0x69

ACCEL_RANGE_G  = 2   # ±2 g   → sensitivity 16384 LSB/g
GYRO_RANGE_DPS = 250 # ±250 °/s → sensitivity 131.2 LSB/°/s

SAMPLE_DELAY_S = 0.5  # seconds between prints
# ───────────────────────────────────────────────────────────────────────────────

# ── BMI160 Register map ────────────────────────────────────────────────────────
REG_CHIP_ID   = 0x00   # Should read 0xD1
REG_PMU_STATUS= 0x03
REG_DATA_GYRO = 0x0C   # GYR_X LSB … ACC_Z MSB  (12 bytes total)
REG_ACC_CONF  = 0x40
REG_ACC_RANGE = 0x41
REG_GYR_CONF  = 0x42
REG_GYR_RANGE = 0x43
REG_CMD       = 0x7E

CMD_SOFT_RESET = 0xB6
CMD_ACC_NORMAL = 0x11  # set accel to normal mode
CMD_GYR_NORMAL = 0x15  # set gyro  to normal mode

# Sensitivities (LSB per unit) for chosen ranges
ACCEL_SENSITIVITY = {2: 16384.0, 4: 8192.0, 8: 4096.0, 16: 2048.0}
GYRO_SENSITIVITY  = {125: 262.4, 250: 131.2, 500: 65.6, 1000: 32.8, 2000: 16.4}

ACC_RANGE_REG = {2: 0x03, 4: 0x05, 8: 0x08, 16: 0x0C}
GYR_RANGE_REG = {2000: 0x00, 1000: 0x01, 500: 0x02, 250: 0x03, 125: 0x04}
# ───────────────────────────────────────────────────────────────────────────────


class BMI160:
    def __init__(self, i2c, addr=BMI160_ADDR,
                 accel_range=2, gyro_range=250):
        self._i2c  = i2c
        self._addr = addr
        self._accel_sens = ACCEL_SENSITIVITY[accel_range]
        self._gyro_sens  = GYRO_SENSITIVITY[gyro_range]

        self._init_sensor(accel_range, gyro_range)

    # ── Low-level I2C helpers ──────────────────────────────────────────────────
    def _write(self, reg, val):
        self._i2c.writeto_mem(self._addr, reg, bytes([val]))

    def _read(self, reg, n=1):
        return self._i2c.readfrom_mem(self._addr, reg, n)

    # ── Initialisation ─────────────────────────────────────────────────────────
    def _init_sensor(self, accel_range, gyro_range):
        # 1. Verify chip ID
        chip_id = self._read(REG_CHIP_ID)[0]
        if chip_id != 0xD1:
            raise RuntimeError(
                f"BMI160 not found at 0x{self._addr:02X}. "
                f"Chip ID = 0x{chip_id:02X} (expected 0xD1). "
                "Check wiring & I2C address."
            )

        # 2. Soft-reset, then wait for startup
        self._write(REG_CMD, CMD_SOFT_RESET)
        time.sleep_ms(100)

        # 3. Bring accelerometer & gyroscope to normal mode
        self._write(REG_CMD, CMD_ACC_NORMAL)
        time.sleep_ms(5)
        self._write(REG_CMD, CMD_GYR_NORMAL)
        time.sleep_ms(80)   # gyro startup ≤ 80 ms

        # 4. Configure accelerometer: ODR = 100 Hz, BWP = normal, no undersampling
        self._write(REG_ACC_CONF, 0x28)  # acc_bwp=normal, acc_odr=100 Hz
        self._write(REG_ACC_RANGE, ACC_RANGE_REG[accel_range])

        # 5. Configure gyroscope: ODR = 100 Hz, BWP = normal
        self._write(REG_GYR_CONF, 0x28)  # gyr_bwp=normal, gyr_odr=100 Hz
        self._write(REG_GYR_RANGE, GYR_RANGE_REG[gyro_range])

        time.sleep_ms(10)
        print(f"BMI160 initialised OK  (chip ID 0x{chip_id:02X})")
        print(f"  Accel range : ±{int(9.81 * (16384 / self._accel_sens))//4}g  "
              f"  Gyro range : ±{int(65536 / (self._gyro_sens * 2))}°/s\n")

    # ── Data reads ─────────────────────────────────────────────────────────────
    def read_raw(self):
        """Return (gx,gy,gz, ax,ay,az) as raw signed 16-bit integers."""
        buf = self._read(REG_DATA_GYRO, 12)
        return struct.unpack('<6h', buf)   # little-endian signed shorts

    def read_accel(self):
        """Acceleration in m/s² (X, Y, Z)."""
        raw = self.read_raw()
        g = 9.80665
        ax = raw[3] / self._accel_sens * g
        ay = raw[4] / self._accel_sens * g
        az = raw[5] / self._accel_sens * g
        return ax, ay, az

    def read_gyro(self):
        """Angular rate in °/s (X, Y, Z)."""
        raw = self.read_raw()
        gx = raw[0] / self._gyro_sens
        gy = raw[1] / self._gyro_sens
        gz = raw[2] / self._gyro_sens
        return gx, gy, gz

    def pitch_roll(self):
        """Pitch and Roll in degrees from accelerometer only (no gyro fusion)."""
        ax, ay, az = self.read_accel()
        pitch = math.degrees(math.atan2(ax, math.sqrt(ay*ay + az*az)))
        roll  = math.degrees(math.atan2(-ay, az))
        return pitch, roll


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("  BMI160 MicroPython Test  –  Raspberry Pi Pico 2")
    print("=" * 55)

    # Set up I2C bus
    i2c = I2C(I2C_ID, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=I2C_FREQ)

    # Scan for devices (optional debug step)
    devices = i2c.scan()
    if devices:
        print(f"I2C devices found: {[hex(d) for d in devices]}")
    else:
        print("No I2C devices found – check wiring!")
        return

    # Initialise sensor
    imu = BMI160(i2c, addr=BMI160_ADDR,
                 accel_range=ACCEL_RANGE_G,
                 gyro_range=GYRO_RANGE_DPS)

    # Read loop
    print(f"{'Accel (m/s²)':^38}  {'Gyro (°/s)':^38}  {'Tilt':^20}")
    print(f"{'Ax':>10}  {'Ay':>10}  {'Az':>10}    "
          f"{'Gx':>10}  {'Gy':>10}  {'Gz':>10}    "
          f"{'Pitch°':>9}  {'Roll°':>9}")
    print("-" * 110)

    while True:
        ax, ay, az = imu.read_accel()
        gx, gy, gz = imu.read_gyro()
        pitch, roll = imu.pitch_roll()

        print(f"{ax:10.3f}  {ay:10.3f}  {az:10.3f}    "
              f"{gx:10.3f}  {gy:10.3f}  {gz:10.3f}    "
              f"{pitch:9.2f}  {roll:9.2f}")

        time.sleep(SAMPLE_DELAY_S)


main()