from machine import Pin, SPI, I2C, time_pulse_us
from nrf24l01 import NRF24L01
import utime
import ustruct
from control.imu import IMU

# 1. Setup LED and BMI160
led = Pin("LED", Pin.OUT)

# HC-SR04 distance sensor
ULTRA_PIN_CANDIDATES = [
    (1, 0),  # user wiring: TRIG=GP1, ECHO=GP0
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

# BMI160
# Try common RP2040 I2C pin mappings in priority order.
# Format: (i2c_id, sda_pin, scl_pin)
IMU_I2C_CANDIDATES = (
    (0, 4, 5),
    (1, 2, 3),
    (1, 6, 7),
)
IMU_ADDR = None  # None = auto-detect, else force 0x68 or 0x69
IMU_ADDR_CANDIDATES = (0x69, 0x68)
IMU_FREQ_CANDIDATES = (400_000, 100_000)
IMU_RETRY_MS = 3000
RADIO_PAYLOAD_SIZE = 19  # Must match Anchor static payload width for auto-ACK.


def init_imu(max_attempts=3):
    addrs = [IMU_ADDR] if IMU_ADDR is not None else list(IMU_ADDR_CANDIDATES)

    for attempt in range(1, max_attempts + 1):
        for i2c_id, sda_pin, scl_pin in IMU_I2C_CANDIDATES:
            for freq in IMU_FREQ_CANDIDATES:
                try:
                    i2c = I2C(i2c_id, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=freq)
                    found = i2c.scan()
                except OSError as exc:
                    if attempt == max_attempts:
                        print(
                            "IMU I2C scan failed on I2C{} GP{}/GP{} @{}Hz: {}".format(
                                i2c_id, sda_pin, scl_pin, freq, exc
                            )
                        )
                    utime.sleep_ms(60)
                    continue

                if attempt == 1:
                    print(
                        "IMU scan I2C{} GP{}/GP{} @{}Hz: {}".format(
                            i2c_id, sda_pin, scl_pin, freq, [hex(a) for a in found]
                        )
                    )

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
                            i2c_id=i2c_id,
                            sda_pin=sda_pin,
                            scl_pin=scl_pin,
                            freq=freq,
                            addr=addr,
                        )
                        print(
                            "BMI160 ready on I2C{} SDA=GP{} SCL=GP{} addr=0x{:02X} @{}Hz".format(
                                i2c_id, sda_pin, scl_pin, addr, freq
                            )
                        )
                        return imu
                    except OSError as exc:
                        if attempt == max_attempts:
                            print(
                                "IMU init failed on I2C{} GP{}/GP{} addr 0x{:02X} @{}Hz ({}/{}): {}".format(
                                    i2c_id,
                                    sda_pin,
                                    scl_pin,
                                    addr,
                                    freq,
                                    attempt,
                                    max_attempts,
                                    exc,
                                )
                            )

                utime.sleep_ms(80)

    print("BMI160 unavailable. Gyro telemetry paused.")
    print("BMI160 I2C wiring check: CS/CSB must be tied to 3.3V (not GND), SAO/SDO to GND=0x68 or 3.3V=0x69.")
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
                payload_size=RADIO_PAYLOAD_SIZE,
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
last_pitch = 0.0
last_roll = 0.0
last_ax = 0.0
last_ay = 0.0
last_az = 0.0
last_gyro_ms = utime.ticks_add(utime.ticks_ms(), -5000)
GYRO_HOLD_MS = 2000
imu_read_fail_count = 0
IMU_READ_FAIL_REINIT = 5
imu_zero_captured = False
pitch_zero = 0.0
roll_zero = 0.0


def _to_i16_cent(value):
    v = int(value * 100)
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v

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

    imu_available = False

    if imu is None:
        if utime.ticks_diff(utime.ticks_ms(), next_imu_retry_ms) >= 0:
            imu = init_imu(max_attempts=1)
            next_imu_retry_ms = utime.ticks_add(utime.ticks_ms(), IMU_RETRY_MS)
        if utime.ticks_diff(utime.ticks_ms(), last_gyro_ms) <= GYRO_HOLD_MS:
            gx, gy, gz = last_gx, last_gy, last_gz
            pitch, roll = last_pitch, last_roll
            ax, ay, az = last_ax, last_ay, last_az
            imu_available = True
        else:
            gx = gy = gz = None
            pitch = roll = None
            ax = ay = az = None
    else:
        try:
            imu_data = imu.read()
            gx, gy, gz = imu_data["gx"], imu_data["gy"], imu_data["gz"]
            raw_pitch, raw_roll = imu_data["pitch"], imu_data["roll"]
            if not imu_zero_captured:
                pitch_zero = raw_pitch
                roll_zero = raw_roll
                imu_zero_captured = True
                print("IMU zero set: pitch0={:.2f} roll0={:.2f}".format(pitch_zero, roll_zero))
            pitch = raw_pitch - pitch_zero
            roll = raw_roll - roll_zero
            ax, ay, az = imu_data["ax"], imu_data["ay"], imu_data["az"]
            imu_available = True
            last_gx, last_gy, last_gz = gx, gy, gz
            last_pitch, last_roll = pitch, roll
            last_ax, last_ay, last_az = ax, ay, az
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
                pitch, roll = last_pitch, last_roll
                ax, ay, az = last_ax, last_ay, last_az
                imu_available = True
            else:
                gx = gy = gz = None
                pitch = roll = None
                ax = ay = az = None

    if imu_available:
        gx_i = _to_i16_cent(gx)
        gy_i = _to_i16_cent(gy)
        gz_i = _to_i16_cent(gz)
        pitch_i = _to_i16_cent(pitch)
        roll_i = _to_i16_cent(roll)
        ax_i = _to_i16_cent(ax)
        ay_i = _to_i16_cent(ay)
        az_i = _to_i16_cent(az)
    else:
        gx_i = gy_i = gz_i = GYRO_MISSING_SENTINEL
        pitch_i = roll_i = GYRO_MISSING_SENTINEL
        ax_i = ay_i = az_i = GYRO_MISSING_SENTINEL
    dist_i = 0xFFFF if distance_cm is None else max(0, min(65534, int(distance_cm)))

    # Packet format: b'T' + <Hhhhhhhhh>
    # distance_cm + gx,gy,gz + pitch,roll + ax,ay,az (all int16 centi-units)
    payload = b"T" + ustruct.pack(
        "<Hhhhhhhhh", dist_i, gx_i, gy_i, gz_i, pitch_i, roll_i, ax_i, ay_i, az_i
    )

    if nrf is None:
        if gx is None:
            print(
                "Telemetry (offline): d={} gx=None gy=None gz=None pitch=None roll=None".format(
                    "None" if distance_cm is None else "{:.1f}cm".format(distance_cm)
                )
            )
        else:
            print(
                "Telemetry (offline): d={} gx={:.2f} gy={:.2f} gz={:.2f} pitch={:.2f} roll={:.2f}".format(
                    "None" if distance_cm is None else "{:.1f}cm".format(distance_cm),
                    gx,
                    gy,
                    gz,
                    pitch,
                    roll,
                )
            )
    else:
        nrf.stop_listening()
        try:
            nrf.send(payload)
            led.toggle()
            if gx is None:
                print(
                    "Sent Telemetry: d={} gx=None gy=None gz=None pitch=None roll=None".format(
                        "None" if distance_cm is None else "{:.1f}cm".format(distance_cm)
                    )
                )
            else:
                print(
                    "Sent Telemetry: d={} gx={:.2f} gy={:.2f} gz={:.2f} pitch={:.2f} roll={:.2f}".format(
                        "None" if distance_cm is None else "{:.1f}cm".format(distance_cm),
                        gx,
                        gy,
                        gz,
                        pitch,
                        roll,
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