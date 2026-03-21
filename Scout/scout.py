from machine import Pin, SPI, I2C
from nrf24l01 import NRF24L01
import utime
import ustruct
from control.imu import IMU

# 1. Setup LED and BMI160
led = Pin("LED", Pin.OUT)

# BMI160 (user-confirmed wiring)
IMU_I2C_ID = 0
IMU_SDA_PIN = 4
IMU_SCL_PIN = 5
IMU_ADDR = None  # None = auto-detect, else force 0x68 or 0x69
IMU_ADDR_CANDIDATES = (0x69, 0x68)
IMU_RETRY_MS = 3000


def _detect_imu_addr():
    forced = [IMU_ADDR] if IMU_ADDR is not None else list(IMU_ADDR_CANDIDATES)

    try:
        i2c = I2C(IMU_I2C_ID, sda=Pin(IMU_SDA_PIN), scl=Pin(IMU_SCL_PIN), freq=400_000)
        devices = i2c.scan()
    except OSError as exc:
        print(f"I2C scan failed for IMU bus: {exc}")
        return None

    if devices:
        print("IMU I2C scan:", [hex(d) for d in devices])
    else:
        print("IMU I2C scan: []")

    for addr in forced:
        if addr in devices:
            try:
                chip = i2c.readfrom_mem(addr, 0x00, 1)[0]
            except OSError:
                continue
            if chip == 0xD1:
                return addr
    return None


def init_imu(max_attempts=3):
    addr = _detect_imu_addr()
    if addr is None:
        print("BMI160 not detected on GP4/GP5 (addr 0x68/0x69).")
        return None

    for attempt in range(1, max_attempts + 1):
        try:
            imu = IMU(
                i2c_id=IMU_I2C_ID,
                sda_pin=IMU_SDA_PIN,
                scl_pin=IMU_SCL_PIN,
                addr=addr,
            )
            print(
                "BMI160 ready on I2C{} SDA=GP{} SCL=GP{} addr=0x{:02X}".format(
                    IMU_I2C_ID, IMU_SDA_PIN, IMU_SCL_PIN, addr
                )
            )
            return imu
        except OSError as exc:
            print(f"IMU init failed ({attempt}/{max_attempts}): {exc}")
            utime.sleep_ms(300)

    print("BMI160 unavailable. Gyro telemetry paused.")
    return None

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

while True:
    if imu is None:
        if utime.ticks_diff(utime.ticks_ms(), next_imu_retry_ms) >= 0:
            imu = init_imu(max_attempts=1)
            next_imu_retry_ms = utime.ticks_add(utime.ticks_ms(), IMU_RETRY_MS)
    else:
        try:
            imu_data = imu.read()
            gx = imu_data["gx"]
            gy = imu_data["gy"]
            gz = imu_data["gz"]
        except OSError as exc:
            print(f"IMU read failed: {exc}")
            imu = None
            next_imu_retry_ms = utime.ticks_add(utime.ticks_ms(), IMU_RETRY_MS)
            gx = gy = gz = 0.0

        gx_i = int(gx * 100)
        gy_i = int(gy * 100)
        gz_i = int(gz * 100)

        # Packet format: b'G' + <int16 gx_cdeg_s><int16 gy_cdeg_s><int16 gz_cdeg_s>
        payload = b"G" + ustruct.pack("<hhh", gx_i, gy_i, gz_i)

        if nrf is None:
            print("Gyro: gx={:.2f} gy={:.2f} gz={:.2f} (radio offline)".format(gx, gy, gz))
        else:
            nrf.stop_listening()
            try:
                nrf.send(payload)
                led.toggle()
                print("Sent Gyro: gx={:.2f} gy={:.2f} gz={:.2f}".format(gx, gy, gz))
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