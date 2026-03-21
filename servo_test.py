# Servo channel 0
# Pico 2 I2C pins: GP0 (SDA), GP1 (SCL)

# Test 30, 90, 150, 90 in a loop

from machine import Pin, I2C
import time

# -------- PCA9685 driver --------
class PCA9685:
    def __init__(self, i2c, address=0x40):
        self.i2c = i2c
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
        self.write(0xFE, prescale_val)               # prescale
        self.write(0x00, old_mode)
        time.sleep_ms(5)
        self.write(0x00, old_mode | 0xA1)

    def set_pwm(self, channel, on, off):
        reg = 0x06 + 4 * channel
        data = bytes([
            on & 0xFF,
            (on >> 8) & 0xFF,
            off & 0xFF,
            (off >> 8) & 0xFF
        ])
        self.i2c.writeto_mem(self.address, reg, data)

# -------- Servo helper --------
class Servo:
    def __init__(self, pca, channel, min_us=500, max_us=2500, freq=50):
        self.pca = pca
        self.channel = channel
        self.min_us = min_us
        self.max_us = max_us
        self.freq = freq

    def angle(self, deg):
        deg = max(0, min(180, deg))
        pulse_us = self.min_us + (self.max_us - self.min_us) * deg / 180
        period_us = 1000000 / self.freq
        counts = int((pulse_us / period_us) * 4096)
        self.pca.set_pwm(self.channel, 0, counts)

# -------- Main --------
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)

print("Scanning I2C...")
print("Found:", [hex(x) for x in i2c.scan()])

pca = PCA9685(i2c)
pca.freq(50)

servo = Servo(pca, channel=0)

while True:
    print("Angle 30")
    servo.angle(30)
    time.sleep(1)

    print("Angle 90")
    servo.angle(90)
    time.sleep(1)

    print("Angle 150")
    servo.angle(150)
    time.sleep(1)

    print("Angle 90")
    servo.angle(90)
    time.sleep(1)