# control/imu.py
# ─────────────────────────────────────────────────────────────
# BMI160 IMU wrapper (I2C).
# Reads raw accelerometer + gyroscope registers and computes
# a complementary-filter tilt angle (pitch/roll) suitable for
# basic orientation-aware navigation on a ground robot.
#
# Usage:
#   from control.imu import IMU
#   imu = IMU()                    # I2C0, SDA=GP4, SCL=GP5
#   data = imu.read()
#   print(imu.to_json(data))       # {"ax":0.1,"ay":0.0,"az":9.8,...}
#
# No third-party library needed — communicates directly with the
# BMI160 over I2C using only machine.I2C.
# ─────────────────────────────────────────────────────────────

from machine import I2C, Pin
import utime
import ujson
import math


# ── BMI160 register map (abridged) ────────────────────────────
_ADDR           = 0x68          # default I2C address (SDO → GND)
_REG_CHIP_ID    = 0x00          # should read 0xD1
_REG_CMD        = 0x7E
_REG_ACC_CONF   = 0x40
_REG_GYR_CONF   = 0x42
_REG_DATA_8     = 0x0C          # start of gyro + accel data block

_CMD_ACC_NORMAL = 0x11          # set acc to normal power
_CMD_GYR_NORMAL = 0x15          # set gyro to normal power
_CMD_SOFTRESET  = 0xB6

_ACC_RANGE_2G   = 0x03          # ±2 g  → 16384 LSB/g
_GYR_RANGE_250  = 0x00          # ±250 °/s → 131 LSB/°/s

_ACC_SCALE      = 9.80665 / 16384.0   # → m/s²
_GYR_SCALE      = 1.0 / 131.0         # → °/s

# Complementary filter coefficient (0.98 = trust gyro 98 %, accel 2 %)
_ALPHA          = 0.98


def _s16(high: int, low: int) -> int:
    """Combine two bytes into a signed 16-bit integer."""
    val = (high << 8) | low
    return val - 65536 if val >= 32768 else val


class IMU:
    """
    BMI160 accelerometer + gyroscope reader with complementary
    filter for pitch and roll estimation.

    Parameters
    ----------
    sda_pin : I2C SDA GP pin  (default 4)
    scl_pin : I2C SCL GP pin  (default 5)
    i2c_id  : I2C bus index   (default 0)
    freq    : I2C clock freq  (default 400_000)
    addr    : BMI160 address  (default 0x68, set to 0x69 if SDO → VCC)
    """

    def __init__(
        self,
        sda_pin: int = 4,
        scl_pin: int = 5,
        i2c_id:  int = 0,
        freq:    int = 400_000,
        addr:    int = _ADDR,
    ):
        self._i2c   = I2C(i2c_id, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=freq)
        self._addr  = addr
        self._pitch = 0.0
        self._roll  = 0.0
        self._t_us  = utime.ticks_us()
        self._init_sensor()

    # ── Init ──────────────────────────────────────────────────

    def _w(self, reg: int, val: int) -> None:
        self._i2c.writeto_mem(self._addr, reg, bytes([val]))

    def _r(self, reg: int, n: int = 1) -> bytes:
        return self._i2c.readfrom_mem(self._addr, reg, n)

    def _init_sensor(self) -> None:
        # Soft-reset
        self._w(_REG_CMD, _CMD_SOFTRESET)
        utime.sleep_ms(100)
        chip_id = self._r(_REG_CHIP_ID)[0]
        if chip_id != 0xD1:
            raise OSError(f"BMI160 not found (chip_id=0x{chip_id:02X}, expected 0xD1)")
        # Power up acc and gyro
        self._w(_REG_CMD, _CMD_ACC_NORMAL); utime.sleep_ms(10)
        self._w(_REG_CMD, _CMD_GYR_NORMAL); utime.sleep_ms(80)
        # Config: ODR 100 Hz, normal bandwidth
        self._w(_REG_ACC_CONF, 0x28)   # acc_odr=100 Hz, bwp=normal
        self._w(_REG_GYR_CONF, 0x28)   # gyr_odr=100 Hz, bwp=normal
        # Range
        self._w(0x41, _ACC_RANGE_2G)
        self._w(0x43, _GYR_RANGE_250)
        utime.sleep_ms(10)

    # ── Raw read ──────────────────────────────────────────────

    def _raw(self) -> tuple[float, float, float, float, float, float]:
        """
        Returns (gx, gy, gz °/s,  ax, ay, az m/s²).
        Register layout 0x0C–0x17: GX_L GX_H GY_L GY_H GZ_L GZ_H
                                    AX_L AX_H AY_L AY_H AZ_L AZ_H
        """
        buf = self._r(_REG_DATA_8, 12)
        gx = _s16(buf[1],  buf[0])  * _GYR_SCALE
        gy = _s16(buf[3],  buf[2])  * _GYR_SCALE
        gz = _s16(buf[5],  buf[4])  * _GYR_SCALE
        ax = _s16(buf[7],  buf[6])  * _ACC_SCALE
        ay = _s16(buf[9],  buf[8])  * _ACC_SCALE
        az = _s16(buf[11], buf[10]) * _ACC_SCALE
        return gx, gy, gz, ax, ay, az

    # ── Complementary filter ──────────────────────────────────

    def update(self) -> dict:
        """
        Read sensor, update complementary filter, return state dict.

        Keys
        ----
        gx, gy, gz      : gyroscope  °/s
        ax, ay, az      : accelerometer m/s²
        pitch, roll     : filtered angles in degrees
        heading_change  : yaw rate °/s (gz) — no magnetometer, so
                          relative only; good for turn detection
        """
        now   = utime.ticks_us()
        dt    = utime.ticks_diff(now, self._t_us) / 1_000_000.0
        self._t_us = now

        gx, gy, gz, ax, ay, az = self._raw()

        # Accel-only pitch/roll (noisy but absolute)
        acc_pitch = math.atan2(ay, math.sqrt(ax*ax + az*az)) * 57.2958
        acc_roll  = math.atan2(-ax, az) * 57.2958

        # Complementary filter: blend gyro integration with accel estimate
        if dt > 0:
            self._pitch = _ALPHA * (self._pitch + gy * dt) + (1 - _ALPHA) * acc_pitch
            self._roll  = _ALPHA * (self._roll  + gx * dt) + (1 - _ALPHA) * acc_roll

        return {
            "gx": round(gx, 2), "gy": round(gy, 2), "gz": round(gz, 2),
            "ax": round(ax, 2), "ay": round(ay, 2), "az": round(az, 2),
            "pitch": round(self._pitch, 1),
            "roll":  round(self._roll,  1),
            "heading_change": round(gz, 2),
        }

    # Keep a simple alias for callers that prefer read()
    def read(self) -> dict:
        return self.update()

    # ── Helpers ───────────────────────────────────────────────

    def is_tilted(self, threshold_deg: float = 30.0) -> bool:
        """True when pitch or roll exceed threshold — robot may be stuck/tipped."""
        return abs(self._pitch) > threshold_deg or abs(self._roll) > threshold_deg

    def heading_rate(self) -> float:
        """Latest yaw rate in °/s (gz). Positive = turning right."""
        _, _, gz, _, _, _ = self._raw()
        return round(gz, 2)

    @staticmethod
    def to_json(data: dict) -> str:
        """
        Compact JSON for serial forwarding.
        Format: {"p":12.3,"r":-1.2,"gz":5.0}
        (pitch, roll, yaw-rate — enough for dashboard visualisation)
        """
        return ujson.dumps({
            "p":  data["pitch"],
            "r":  data["roll"],
            "gz": data["heading_change"],
        })