# control/safety.py
# ─────────────────────────────────────────────────────────────
# HC-SR04 ultrasonic safety layer.
#
# Usage (from Scout.py or any file):
#
#   from control.safety import UltrasonicGuard
#   guard = UltrasonicGuard()          # default TRIG=GP14, ECHO=GP15
#   safe, distance = guard.check("FORWARD")
#   if not safe:
#       print(f"Blocked at {distance:.1f} cm")
#
# check() returns (True, dist) when movement is permitted and
# (False, dist) when the safety override fires.
# ─────────────────────────────────────────────────────────────

from machine import Pin
import utime


class UltrasonicGuard:
    """
    HC-SR04 obstacle guard with configurable threshold.

    Parameters
    ----------
    trig_pin       : GP pin for the TRIG line          (default 14)
    echo_pin       : GP pin for the ECHO line          (default 15)
    threshold_cm   : Stop distance in centimetres      (default 20)
    timeout_us     : Max echo wait in microseconds     (default 30 000)
    blocked_cmds   : Commands that trigger the safety  (default {"FORWARD"})
                     BCK / LEFT / RIGHT always pass so the robot can escape.
    """

    def __init__(
        self,
        trig_pin: int = 14,
        echo_pin: int = 15,
        threshold_cm: float = 20.0,
        timeout_us: int = 30_000,
        blocked_cmds: set[str] | None = None,
    ):
        self._trig       = Pin(trig_pin, Pin.OUT)
        self._echo       = Pin(echo_pin, Pin.IN)
        self.threshold   = threshold_cm
        self.timeout_us  = timeout_us
        self.blocked_cmds = blocked_cmds if blocked_cmds is not None else {"FORWARD"}
        self._trig.low()

    # ── Sensor read ──────────────────────────────────────────

    def distance_cm(self) -> float:
        """
        Pulse TRIG and measure ECHO to get distance in cm.
        Returns 999.0 on timeout (sensor absent / target too far).
        """
        trig = self._trig
        echo = self._echo

        # 10 µs trigger pulse
        trig.low()
        utime.sleep_us(2)
        trig.high()
        utime.sleep_us(10)
        trig.low()

        # Wait for echo to go high
        t0 = utime.ticks_us()
        while echo.value() == 0:
            if utime.ticks_diff(utime.ticks_us(), t0) > self.timeout_us:
                return 999.0

        # Measure pulse width
        start = utime.ticks_us()
        while echo.value() == 1:
            if utime.ticks_diff(utime.ticks_us(), start) > self.timeout_us:
                return 999.0

        duration = utime.ticks_diff(utime.ticks_us(), start)
        # Speed of sound: 343 m/s ≈ 0.0343 cm/µs; round-trip ÷ 2
        return round((duration * 0.0343) / 2, 1)

    # ── Safety gate ──────────────────────────────────────────

    def check(self, command: str) -> tuple[bool, float]:
        """
        Evaluate whether the requested command is safe to execute.

        Returns
        -------
        (True,  dist)  — command is allowed; caller should run motors
        (False, dist)  — obstacle detected; caller should stop & log
        """
        dist = self.distance_cm()
        if dist < self.threshold and command in self.blocked_cmds:
            return False, dist
        return True, dist

    def status_line(self, command: str) -> str:
        """
        Convenience: returns the formatted status string expected by
        Scout's serial output without the caller needing to call
        distance_cm() separately.

        Example return values:
          "Distance: 15.0cm | Status: BLOCKED"
          "Distance: 50.0cm | Status: MOVING"
        """
        safe, dist = self.check(command)
        label = "BLOCKED" if not safe else command if command != "IDLE" else "IDLE"
        return f"Distance: {dist}cm | Status: {label}"