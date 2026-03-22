from machine import Pin, SPI, I2C, time_pulse_us
from nrf24l01 import NRF24L01
import utime
import ustruct
from control.imu import IMU

# 1. Setup LED and BMI160
led = Pin("LED", Pin.OUT)

# HC-SR04 distance sensor
ULTRA_PIN_CANDIDATES = [
    (6, 7),  # original sensor_test.py mapping
    (3, 2),  # mapping used in older Scout revisions
    (2, 3),
]
DIST_TIMEOUT_US = 30000
DIST_HOLD_MS = 1500
GYRO_MISSING_SENTINEL = -32768

TRIG_PIN, ECHO_PIN = ULTRA_PIN_CANDIDATES[0]
trig = Pin(TRIG_PIN, Pin.OUT)
echo = Pin(ECHO_PIN, Pin.IN)
trig.value(0)
utime.sleep_ms(2)

# BMI160 (user-confirmed wiring)
IMU_I2C_ID = 0
IMU_SDA_PIN = 4
IMU_SCL_PIN = 5
IMU_ADDR = None  # None = auto-detect, else force 0x68 or 0x69
IMU_ADDR_CANDIDATES = (0x69, 0x68)
IMU_FREQ_CANDIDATES = (400_000, 100_000)
IMU_RETRY_MS = 3000


def init_imu(max_attempts=3):
    addrs = [IMU_ADDR] if IMU_ADDR is not None else list(IMU_ADDR_CANDIDATES)

    for attempt in range(1, max_attempts + 1):
        for freq in IMU_FREQ_CANDIDATES:
            try:
                i2c = I2C(IMU_I2C_ID, sda=Pin(IMU_SDA_PIN), scl=Pin(IMU_SCL_PIN), freq=freq)
                found = i2c.scan()
            except OSError as exc:
                if attempt == max_attempts:
                    print("IMU I2C scan failed at {} Hz: {}".format(freq, exc))
                utime.sleep_ms(100)
                continue

            if attempt == 1:
                print("IMU scan @{}Hz: {}".format(freq, [hex(a) for a in found]))

            for addr in addrs:
                if addr not in found:
                    continue
                try:
                    chip_id = i2c.readfrom_mem(addr, 0x00, 1)[0]
                except OSError:
                    continue
                if chip_id != 0xD1:
                    continue

                try:
                    imu = IMU(
                        i2c_id=IMU_I2C_ID,
                        sda_pin=IMU_SDA_PIN,
                        scl_pin=IMU_SCL_PIN,
                        freq=freq,
                        addr=addr,
                    )
                    print(
                        "BMI160 ready on I2C{} SDA=GP{} SCL=GP{} addr=0x{:02X} @{}Hz".format(
                            IMU_I2C_ID, IMU_SDA_PIN, IMU_SCL_PIN, addr, freq
                        )
                    )
                    return imu
                except OSError as exc:
                    if attempt == max_attempts:
                        print(
                            "IMU init failed at addr 0x{:02X} @{}Hz ({}/{}): {}".format(
                                addr, freq, attempt, max_attempts, exc
                            )
                        )

            utime.sleep_ms(150)

    print("BMI160 unavailable. Gyro telemetry paused.")
    return None


def read_distance_cm():
    trig.value(0)
    utime.sleep_us(2)
    trig.value(1)
    utime.sleep_us(10)
    trig.value(0)

    duration = time_pulse_us(echo, 1, DIST_TIMEOUT_US)
    if duration < 0:
        return None
    return duration / 58.0


def detect_ultrasonic_pins():
    global TRIG_PIN, ECHO_PIN, trig, echo

    for trig_pin, echo_pin in ULTRA_PIN_CANDIDATES:
        t = Pin(trig_pin, Pin.OUT)
        e = Pin(echo_pin, Pin.IN)
        t.value(0)
        utime.sleep_ms(2)

        ok = False
        for _ in range(3):
            t.value(0)
            utime.sleep_us(2)
            t.value(1)
            utime.sleep_us(10)
            t.value(0)
            dur = time_pulse_us(e, 1, DIST_TIMEOUT_US)
            if dur > 0:
                ok = True
                break
            utime.sleep_ms(30)

        if ok:
            TRIG_PIN, ECHO_PIN = trig_pin, echo_pin
            trig, echo = t, e
            print("HC-SR04 detected on TRIG=GP{} ECHO=GP{}".format(TRIG_PIN, ECHO_PIN))
            return True

    print("HC-SR04 not detected on candidate pins:", ULTRA_PIN_CANDIDATES)
    return False

# 2. Setup SPI and Radio
spi = SPI(0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cfg = {"csn": 14, "ce": 17}


def init_radio(max_attempts=5):
    for attempt in range(1, max_attempts + 1):
        try:
            return NRF24L01(
                spi,
                Pin(cfg["csn"]),
                Pin(cfg["ce"]),
                payload_size=16,
                spi_baudrate=1_000_000,
                startup_delay_ms=120,
            )
        except OSError as exc:
            print(f"Radio init failed ({attempt}/{max_attempts}): {exc}")
            utime.sleep_ms(300)

    print(
        "nRF24L01 unavailable. Running without radio. Verify: VCC=3.3V, GND, SCK=GP18, MOSI=GP19, MISO=GP16, CSN=GP14, CE=GP17."
    )
    return None


nrf = init_radio()
imu = init_imu()
next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)
next_imu_retry_ms = utime.ticks_add(utime.ticks_ms(), IMU_RETRY_MS)

# Communication Pipes
pipes = (b"\xe1\xf0\xf0\xf0\xf0", b"\xd2\xf0\xf0\xf0\xf0")


def configure_radio(radio):
    radio.open_tx_pipe(pipes[0])
    radio.open_rx_pipe(1, pipes[1])


if nrf is not None:
    configure_radio(nrf)

print("Scout Sending Mode...")
detect_ultrasonic_pins()

last_distance_cm = None
last_distance_ms = utime.ticks_ms()
no_echo_count = 0
last_gx = 0.0
last_gy = 0.0
last_gz = 0.0
last_gyro_ms = utime.ticks_add(utime.ticks_ms(), -5000)
GYRO_HOLD_MS = 2000
imu_read_fail_count = 0
IMU_READ_FAIL_REINIT = 5

while True:
    distance_now = read_distance_cm()

    if distance_now is not None:
        last_distance_cm = distance_now
        last_distance_ms = utime.ticks_ms()
        no_echo_count = 0
        distance_cm = distance_now
    else:
        no_echo_count += 1
        if (
            last_distance_cm is not None
            and utime.ticks_diff(utime.ticks_ms(), last_distance_ms) <= DIST_HOLD_MS
        ):
            # Keep telemetry stable through short ultrasonic dropouts.
            distance_cm = last_distance_cm
        else:
            distance_cm = None

        if no_echo_count % 8 == 0:
            print(
                "HC-SR04 timeout on TRIG=GP{} ECHO=GP{} (check wiring/target range)".format(
                    TRIG_PIN, ECHO_PIN
                )
            )

    gyro_available = False

    if imu is None:
        if utime.ticks_diff(utime.ticks_ms(), next_imu_retry_ms) >= 0:
            imu = init_imu(max_attempts=1)
            next_imu_retry_ms = utime.ticks_add(utime.ticks_ms(), IMU_RETRY_MS)
        if utime.ticks_diff(utime.ticks_ms(), last_gyro_ms) <= GYRO_HOLD_MS:
            gx, gy, gz = last_gx, last_gy, last_gz
            gyro_available = True
        else:
            gx = gy = gz = None
    else:
        try:
            gx, gy, gz = imu.read_gyro()
            gyro_available = True
            last_gx, last_gy, last_gz = gx, gy, gz
            last_gyro_ms = utime.ticks_ms()
            imu_read_fail_count = 0
        except OSError as exc:
            imu_read_fail_count += 1
            print("IMU read failed ({}/{}): {}".format(imu_read_fail_count, IMU_READ_FAIL_REINIT, exc))
            if imu_read_fail_count >= IMU_READ_FAIL_REINIT:
                imu = None
                imu_read_fail_count = 0
                next_imu_retry_ms = utime.ticks_add(utime.ticks_ms(), IMU_RETRY_MS)
            if utime.ticks_diff(utime.ticks_ms(), last_gyro_ms) <= GYRO_HOLD_MS:
                gx, gy, gz = last_gx, last_gy, last_gz
                gyro_available = True
            else:
                gx = gy = gz = None

    if gyro_available:
        gx_i = int(gx * 100)
        gy_i = int(gy * 100)
        gz_i = int(gz * 100)
    else:
        gx_i = gy_i = gz_i = GYRO_MISSING_SENTINEL
    dist_i = 0xFFFF if distance_cm is None else max(0, min(65534, int(distance_cm)))

    # Packet format: b'T' + <uint16 distance_cm> + <int16 gx,gy,gz centi-deg/s>
    payload = b"T" + ustruct.pack("<Hhhh", dist_i, gx_i, gy_i, gz_i)

    if nrf is None:
        if gx is None:
            print(
                "Telemetry (offline): d={} gx=None gy=None gz=None".format(
                    "None" if distance_cm is None else "{:.1f}cm".format(distance_cm)
                )
            )
        else:
            print(
                "Telemetry (offline): d={} gx={:.2f} gy={:.2f} gz={:.2f}".format(
                    "None" if distance_cm is None else "{:.1f}cm".format(distance_cm),
                    gx,
                    gy,
                    gz,
                )
            )
    else:
        nrf.stop_listening()
        try:
            nrf.send(payload)
            led.toggle()
            if gx is None:
                print(
                    "Sent Telemetry: d={} gx=None gy=None gz=None".format(
                        "None" if distance_cm is None else "{:.1f}cm".format(distance_cm)
                    )
                )
            else:
                print(
                    "Sent Telemetry: d={} gx={:.2f} gy={:.2f} gz={:.2f}".format(
                        "None" if distance_cm is None else "{:.1f}cm".format(distance_cm),
                        gx,
                        gy,
                        gz,
                    )
                )
        except OSError as exc:
            msg = str(exc)
            if msg in ("send failed", "timed out"):
                print("Anchor not responding (packet not acknowledged)")
            else:
                print("Radio hardware error; entering offline mode")
                nrf = None
                next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)

    # background retry if radio was unavailable
    if nrf is None and utime.ticks_diff(utime.ticks_ms(), next_retry_ms) >= 0:
        nrf = init_radio(max_attempts=1)
        if nrf is not None:
            configure_radio(nrf)
        next_retry_ms = utime.ticks_add(utime.ticks_ms(), 5000)
            
    utime.sleep_ms(250)