from machine import Pin, time_pulse_us
import utime

trig = Pin(3, Pin.OUT)
echo = Pin(2, Pin.IN)

def distance_cm():
    trig.low()
    utime.sleep_us(2)
    trig.high()
    utime.sleep_us(10)
    trig.low()

    duration = time_pulse_us(echo, 1, 30000)  # timeout 30ms
    if duration < 0:
        return None
    return (duration * 0.0343) / 2

while True:
    d = distance_cm()
    if d is None:
        print("No echo / out of range")
    else:
        print("Distance: %.1f cm" % d)
    utime.sleep_ms(500)