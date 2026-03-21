# TRIG on GP6
# ECHO on GP7

from machine import Pin, time_pulse_us
import time

TRIG_PIN = 6
ECHO_PIN = 7

trig = Pin(TRIG_PIN, Pin.OUT)
echo = Pin(ECHO_PIN, Pin.IN)

trig.value(0)
time.sleep_ms(2)

def read_distance_cm():
    # send 10us trigger pulse
    trig.value(0)
    time.sleep_us(2)
    trig.value(1)
    time.sleep_us(10)
    trig.value(0)

    # measure echo pulse width
    duration = time_pulse_us(echo, 1, 30000)

    if duration < 0:
        return None

    # sound speed conversion
    distance_cm = duration / 58.0
    return distance_cm

while True:
    d = read_distance_cm()
    if d is None:
        print("No reading")
    else:
        print("Distance: {:.1f} cm".format(d))
    time.sleep(0.5)