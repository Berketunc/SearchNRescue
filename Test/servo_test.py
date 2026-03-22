# Direct servo drive on Pico GP3 (no I2C / no PCA9685)
# Wiring:
#   Signal -> GP3
#   V+     -> external 5V
#   GND    -> external GND
#   Pico GND must be tied to external GND

from machine import Pin, PWM
import time

SERVO_PIN = 2
FREQ_HZ = 50

# Tune if your servo needs a different travel range.
MIN_US = 600
MAX_US = 2400

pwm = PWM(Pin(SERVO_PIN))
pwm.freq(FREQ_HZ)


def set_pulse_us(us):
    duty = int(us * 65535 / 20000)  # 20 ms frame at 50 Hz
    pwm.duty_u16(duty)


def set_angle(deg):
    deg = max(0, min(180, deg))
    pulse_us = int(MIN_US + (MAX_US - MIN_US) * deg / 180)
    set_pulse_us(pulse_us)


print("Direct servo test on GP3")
print("Pattern: 30 -> 90 -> 150 -> 90")

while True:
    print("Angle 30")
    set_angle(30)
    time.sleep(1)

    print("Angle 90")
    set_angle(90)
    time.sleep(1)

    print("Angle 150")
    set_angle(150)
    time.sleep(1)

    print("Angle 90")
    set_angle(90)
    time.sleep(1)