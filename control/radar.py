# control/radar.py
# ─────────────────────────────────────────────────────────────
# Servo-mounted HC-SR04 radar.
#
# Wiring (matches your working test files exactly):
#   HC-SR04  TRIG → GP6
#   HC-SR04  ECHO → GP7
#   PCA9685  SDA  → GP0
#   PCA9685  SCL  → GP1
#   Servo         → PCA9685 channel 0
#
# Usage:
#   from control.radar import Radar
#   radar = Radar()
#   scan = radar.sweep()          # list of (angle, dist_cm)
#   print(radar.to_json(scan))    # {"r":[[0,45.2],[15,33.1],...]}
# ─────────────────────────────────────────────────────────────

from machine import Pin, I2C, time_pulse_us
import time
import ujson


# ══════════════════════════════════════════════════════════════
#  PCA9685 driver  (from your working servo_test.py)
# ══════════════════════════════════════════════════════════════

class PCA9685:
    def __init__(self, i2c, address=0x40):
        self.i2c     = i2c
        self.address = address
        self.reset()

    def write(self, reg, value):
        self.i2c.writeto_mem(self.address, reg, bytes([value]))

    def read(self, reg):
        return self.i2c.readfrom_mem(self.address, reg, 1)[0]

    def reset(self):
        self.write(0x00, 0x00)

    def freq(self, freq_hz):
        prescale_val = int(25000000.0 / 4096 / freq_hz - 1 + 0.5)
        old_mode = self.read(0x00)
        self.write(0x00, (old_mode & 0x7F) | 0x10)   # sleep
        self.write(0xFE, prescale_val)                # set prescale
        self.write(0x00, old_mode)                    # wake
        time.sleep_ms(5)
        self.write(0x00, old_mode | 0xA1)             # restart

    def set_pwm(self, channel, on, off):
        reg  = 0x06 + 4 * channel
        data = bytes([
            on  & 0xFF, (on  >> 8) & 0xFF,
            off & 0xFF, (off >> 8) & 0xFF,
        ])
        self.i2c.writeto_mem(self.address, reg, data)


# ══════════════════════════════════════════════════════════════
#  Servo helper  (from your working servo_test.py)
# ══════════════════════════════════════════════════════════════

class _Servo:
    def __init__(self, pca, channel=0, min_us=500, max_us=2500, freq=50):
        self.pca     = pca
        self.channel = channel
        self.min_us  = min_us
        self.max_us  = max_us
        self.freq    = freq

    def angle(self, deg):
        deg       = max(0, min(180, deg))
        pulse_us  = self.min_us + (self.max_us - self.min_us) * deg / 180
        period_us = 1_000_000 / self.freq
        counts    = int((pulse_us / period_us) * 4096)
        self.pca.set_pwm(self.channel, 0, counts)


# ══════════════════════════════════════════════════════════════
#  Radar
# ══════════════════════════════════════════════════════════════

class Radar:
    """
    Servo-mounted HC-SR04 scanner via PCA9685 over I2C.

    Parameters
    ----------
    trig_pin    : GP pin for HC-SR04 TRIG         (default 6)
    echo_pin    : GP pin for HC-SR04 ECHO         (default 7)
    sda_pin     : I2C SDA for PCA9685             (default 0)
    scl_pin     : I2C SCL for PCA9685             (default 1)
    i2c_id      : I2C bus index                   (default 0)
    pca_addr    : PCA9685 I2C address             (default 0x40)
    servo_ch    : PCA9685 channel for servo       (default 0)
    min_angle   : Sweep start in degrees          (default 0)
    max_angle   : Sweep end in degrees            (default 180)
    step        : Degrees between readings        (default 15)
    settle_ms   : Wait after moving servo (ms)   (default 80)
    """

    def __init__(
        self,
        trig_pin:  int = 6,
        echo_pin:  int = 7,
        sda_pin:   int = 0,
        scl_pin:   int = 1,
        i2c_id:    int = 0,
        pca_addr:  int = 0x40,
        servo_ch:  int = 0,
        min_angle: int = 0,
        max_angle: int = 180,
        step:      int = 15,
        settle_ms: int = 80,
    ):
        # HC-SR04
        self._trig = Pin(trig_pin, Pin.OUT)
        self._echo = Pin(echo_pin, Pin.IN)
        self._trig.value(0)

        # PCA9685 + servo
        i2c         = I2C(i2c_id, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=400_000)
        pca         = PCA9685(i2c, address=pca_addr)
        pca.freq(50)
        self._servo = _Servo(pca, channel=servo_ch)

        self.min_angle = min_angle
        self.max_angle = max_angle
        self.step      = step
        self.settle_ms = settle_ms

        # Park at centre on startup
        self._servo.angle(90)
        time.sleep_ms(400)

    # ── HC-SR04 ping ──────────────────────────────────────────
    # Mirrors sensor_test.py exactly: time_pulse_us + /58.0

    def ping_cm(self):
        """
        Single distance reading in cm.
        Returns None if echo times out (no obstacle in range).
        """
        self._trig.value(0)
        time.sleep_us(2)
        self._trig.value(1)
        time.sleep_us(10)
        self._trig.value(0)

        duration = time_pulse_us(self._echo, 1, 30_000)

        if duration < 0:
            return None

        return duration / 58.0

    # ── Servo move ────────────────────────────────────────────

    def move_to(self, angle):
        """Move servo to angle and wait for it to settle."""
        self._servo.angle(angle)
        time.sleep_ms(self.settle_ms)

    # ── Full sweep ────────────────────────────────────────────

    def sweep(self):
        """
        Sweep from min_angle to max_angle, ping at each step.
        Returns list of (angle, dist_cm).
        dist_cm is None when the echo timed out (open space).
        """
        results = []
        for angle in range(self.min_angle, self.max_angle + 1, self.step):
            self._servo.angle(angle)
            time.sleep_ms(self.settle_ms)
            dist = self.ping_cm()
            results.append((angle, dist))

        # Park back at centre
        self._servo.angle(90)
        return results

    # ── Analysis helpers ──────────────────────────────────────

    @staticmethod
    def nearest(scan):
        """
        (angle, dist_cm) of the closest valid reading.
        Ignores None (timeout) readings.
        Returns (90, None) if all readings timed out.
        """
        valid = [(a, d) for a, d in scan if d is not None]
        if not valid:
            return (90, None)
        return min(valid, key=lambda p: p[1])

    @staticmethod
    def clear_arc(scan, min_dist_cm=30.0, min_angle=60, max_angle=120):
        """
        True if every valid reading in [min_angle, max_angle]
        is farther than min_dist_cm.
        None readings (timeouts) count as clear — nothing was seen.
        """
        arc   = [d for a, d in scan if min_angle <= a <= max_angle]
        valid = [d for d in arc if d is not None]
        if not arc:
            return False
        if not valid:
            return True   # all timeouts in arc = open space
        return min(valid) >= min_dist_cm

    # ── Serialisation for dashboard ───────────────────────────

    @staticmethod
    def to_json(scan):
        """
        Compact JSON for serial → dashboard.
        None distances encoded as 400 (max display range).
        Format: {"r": [[angle, dist], ...]}
        """
        payload = [[a, round(d, 1) if d is not None else 400] for a, d in scan]
        return ujson.dumps({"r": payload})