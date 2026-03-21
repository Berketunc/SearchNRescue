# radar.py
import time

class RadarScanner:
    def __init__(self, pca, servo_channel, sensor):
        self.pca = pca
        self.servo_channel = servo_channel
        self.sensor = sensor

        # MG90S servo pulse width range (PCA9685 PWM values)
        # 0° = ~150, 180° = ~600 (at 50Hz)
        self.min_pulse = 150   # 0 degrees
        self.max_pulse = 600   # 180 degrees

    def angle_to_pwm(self, angle):
        angle = max(0, min(180, angle))
        return int(self.min_pulse + (angle / 180) * (self.max_pulse - self.min_pulse))

    def set_angle(self, angle):
        pwm = self.angle_to_pwm(angle)
        self.pca.set_pwm(self.servo_channel, 0, pwm)
        time.sleep(0.3)  # allow servo to settle + HC-SR04 measurement time

    def read_distance(self):
        try:
            # HC-SR04 needs ~60ms between measurements for stable readings
            d = self.sensor.read_cm()
            # Validate HC-SR04 reading (typical range: 2-400cm)
            if d < 2 or d > 400:
                return -1
            return d
        except Exception:
            return -1

    def scan(self, start=20, end=160, step=20):
        results = []
        for angle in range(start, end + 1, step):
            self.set_angle(angle)
            distance = self.read_distance()
            results.append((angle, distance))
        return results

    def center(self):
        self.set_angle(90)

    def sweep_and_find_best_direction(self, start=20, end=160, step=20):
        scan = self.scan(start, end, step)

        valid = [item for item in scan if item[1] > 0]
        if not valid:
            return scan, None

        best = max(valid, key=lambda x: x[1])  # largest distance = clearest path
        return scan, best