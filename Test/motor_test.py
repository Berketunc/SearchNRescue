"""Quick motor channel diagnostic for L298N HW-095 on Pico."""

import utime
from control.motors import MotorPair


def main():
    motors = MotorPair(
        a_in1=9,
        a_in2=10,
        a_pwm=11,
        b_in1=6,
        b_in2=12,
        b_pwm=13,
    )

    print("Motor channel diagnostic test")
    speed = 65535
    print("Sequence: LEFT only -> RIGHT only -> BOTH forward -> STOP")
    print("Speed:", speed)
    print("Press Ctrl+C to stop early")

    try:
        print("1) LEFT motor forward for 3s (RIGHT stopped)")
        motors.left.forward(speed)
        motors.right.stop()
        utime.sleep(3)

        print("2) RIGHT motor forward for 3s (LEFT stopped)")
        motors.left.stop()
        motors.right.forward(speed)
        utime.sleep(3)

        print("3) BOTH motors forward for 3s")
        motors.forward(speed)
        utime.sleep(3)

        print("4) STOP")
        motors.stop()
        print("Diagnostic complete")
    except KeyboardInterrupt:
        print("Stopping motors")
        motors.stop()


if __name__ == "__main__":
    main()
