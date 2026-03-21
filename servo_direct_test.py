# Basic positional servo (blue servo) direct from Pico GP0
# Wiring:
#   Signal -> GP0
#   V+     -> 5V external
#   GND    -> external GND
#   Pico GND must be connected to same GND

from machine import Pin, PWM
import time

SERVO_PIN = 0
FREQ_HZ = 50

# Positional servo calibration (adjust if endpoints are too far)
MIN_US = 600
MAX_US = 2550

pwm = PWM(Pin(SERVO_PIN))
pwm.freq(FREQ_HZ)


def set_pulse_us(us):
    duty = int(us * 65535 / 20000)  # 20ms frame at 50Hz
    pwm.duty_u16(duty)


def set_angle(deg):
    if deg < 0:
        deg = 0
    if deg > 180:
        deg = 180
    us = int(MIN_US + (MAX_US - MIN_US) * deg / 180)
    set_pulse_us(us)


print("Positional servo on GP0")
print("Pattern: 0° -> wait 2s -> 180° -> wait 2s")
print("MIN_US=", MIN_US, "MAX_US=", MAX_US)

while True:
    print("90")
    set_angle(90)
    time.sleep(2)

    print("180")
    set_angle(180)
    time.sleep(2)

    print("90")
    set_angle(90)
    time.sleep(2)

    print("0")
    set_angle(0)
    time.sleep(2)