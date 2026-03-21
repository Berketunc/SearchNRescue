# control/motors.py
# ─────────────────────────────────────────────────────────────
# L298N dual H-bridge motor driver.
#
# Usage (from Scout.py or any file):
#
#   from control.motors import MotorPair
#   motors = MotorPair()
#   motors.forward()
#   motors.stop()
#
# Speed values are 0–65535 (machine.PWM.duty_u16 range).
# Defaults are set below the MotorPair class — adjust to taste.
# ─────────────────────────────────────────────────────────────

from machine import Pin, PWM


# ── Sane speed defaults ──────────────────────────────────────
# ~61 % duty — firm but not full-throttle on first move
SPEED_NORMAL: int = 40_000
# ~49 % duty — gentler for turns
SPEED_TURN:   int = 32_000


class _Motor:
    """One side of an L298N (two direction pins + one PWM enable)."""

    def __init__(self, in1: int, in2: int, pwm_pin: int, freq: int = 1_000):
        self._in1 = Pin(in1, Pin.OUT)
        self._in2 = Pin(in2, Pin.OUT)
        self._pwm = PWM(Pin(pwm_pin))
        self._pwm.freq(freq)
        self.stop()

    def forward(self, speed: int = SPEED_NORMAL) -> None:
        self._in1.high()
        self._in2.low()
        self._pwm.duty_u16(speed)

    def backward(self, speed: int = SPEED_NORMAL) -> None:
        self._in1.low()
        self._in2.high()
        self._pwm.duty_u16(speed)

    def stop(self) -> None:
        self._in1.low()
        self._in2.low()
        self._pwm.duty_u16(0)


class MotorPair:
    """
    Two-motor differential drive (left + right wheels).

    Default wiring (L298N on SPI0-free GP pins):
      Motor A (left)  — IN1=GP8,  IN2=GP9,  ENA=GP10
      Motor B (right) — IN3=GP11, IN4=GP12, ENB=GP13

    Override any pin by passing keyword arguments to __init__.
    """

    def __init__(
        self,
        a_in1: int = 8,
        a_in2: int = 9,
        a_pwm: int = 10,
        b_in1: int = 11,
        b_in2: int = 12,
        b_pwm: int = 13,
        pwm_freq: int = 1_000,
    ):
        self.left  = _Motor(a_in1, a_in2, a_pwm, pwm_freq)
        self.right = _Motor(b_in1, b_in2, b_pwm, pwm_freq)

    # ── Named manoeuvres ─────────────────────────────────────

    def forward(self, speed: int = SPEED_NORMAL) -> None:
        self.left.forward(speed)
        self.right.forward(speed)

    def backward(self, speed: int = SPEED_NORMAL) -> None:
        self.left.backward(speed)
        self.right.backward(speed)

    def left_turn(self, speed: int = SPEED_TURN) -> None:
        """Pivot left: left wheel back, right wheel forward."""
        self.left.backward(speed)
        self.right.forward(speed)

    def right_turn(self, speed: int = SPEED_TURN) -> None:
        """Pivot right: left wheel forward, right wheel back."""
        self.left.forward(speed)
        self.right.backward(speed)

    def stop(self) -> None:
        self.left.stop()
        self.right.stop()

    # ── Generic dispatch (accepts a command string) ───────────

    def execute(self, command: str) -> None:
        """
        Drive the motors based on a command string.
        Raises ValueError for unknown commands.
        """
        dispatch = {
            "FORWARD": self.forward,
            "BACK":    self.backward,
            "LEFT":    self.left_turn,
            "RIGHT":   self.right_turn,
            "IDLE":    self.stop,
        }
        action = dispatch.get(command)
        if action is None:
            raise ValueError(f"Unknown command: {command!r}")
        action()