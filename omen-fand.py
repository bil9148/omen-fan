#!/usr/bin/env python3

import logging
import logging.handlers
import os
import signal
import sys
import time
from time import sleep
import tomlkit
from bisect import bisect_left

log = logging.getLogger("omen-fand")
log.setLevel(logging.INFO)
_handler = logging.handlers.SysLogHandler(address="/dev/log", facility=logging.handlers.SysLogHandler.LOG_DAEMON)
_handler.setFormatter(logging.Formatter("omen-fand: %(message)s"))
log.addHandler(_handler)

ECIO_FILE = "/sys/kernel/debug/ec/ec0/io"
IPC_FILE = "/run/omen-fand.pid"
CONFIG_FILE = "/etc/omen-fan/config.toml"

FAN1_OFFSET = 52  # 0x34
FAN2_OFFSET = 53  # 0x35
BIOS_OFFSET = 98  # 0x62
TIMER_OFFSET = 99  # 0x63
CPU_TEMP_OFFSET = 87  # 0x57
GPU_TEMP_OFFSET = 183  # 0xB7

FAN1_MAX = 55
FAN2_MAX = 57

with open(CONFIG_FILE, "r") as file:
    doc = tomlkit.loads(file.read())
    TEMP_CURVE = doc["service"]["TEMP_CURVE"].unwrap()
    SPEED_CURVE = doc["service"]["SPEED_CURVE"].unwrap()
    IDLE_SPEED = doc["service"]["IDLE_SPEED"].unwrap()
    POLL_INTERVAL = doc["service"]["POLL_INTERVAL"].unwrap()

# Validate config
if len(TEMP_CURVE) != len(SPEED_CURVE):
    print("  ERROR: TEMP_CURVE and SPEED_CURVE must have the same length.")
    sys.exit(1)
if len(TEMP_CURVE) < 2:
    print("  ERROR: Curves must have at least 2 points.")
    sys.exit(1)
if not all(TEMP_CURVE[i] <= TEMP_CURVE[i + 1] for i in range(len(TEMP_CURVE) - 1)):
    print("  ERROR: TEMP_CURVE must be in ascending order.")
    sys.exit(1)
if not all(0 <= s <= 100 for s in SPEED_CURVE):
    print("  ERROR: SPEED_CURVE values must be between 0 and 100.")
    sys.exit(1)
if not 0 <= IDLE_SPEED <= 100:
    print("  ERROR: IDLE_SPEED must be between 0 and 100.")
    sys.exit(1)
if POLL_INTERVAL <= 0:
    print("  ERROR: POLL_INTERVAL must be greater than 0.")
    sys.exit(1)

# Precalculate slopes to reduce compute time.
slope = []
for i in range(1, len(TEMP_CURVE)):
    speed_diff = SPEED_CURVE[i] - SPEED_CURVE[i - 1]
    temp_diff = TEMP_CURVE[i] - TEMP_CURVE[i - 1]
    slope_val = round(speed_diff / temp_diff, 2)
    slope.append(slope_val)


def is_root():
    if os.geteuid() != 0:
        print("  Root access is required for this service.")
        print("  Please run this service as root.")
        sys.exit(1)


def sig_handler(signum, frame):
    log.info("received signal %d, shutting down", signum)
    try:
        os.remove(IPC_FILE)
    except OSError:
        pass
    bios_control(True)
    sys.exit()


def update_fan(speed1, speed2):
    with open(ECIO_FILE, "r+b") as ec:
        ec.seek(FAN1_OFFSET)
        ec.write(bytes([int(speed1)]))
        ec.seek(FAN2_OFFSET)
        ec.write(bytes([int(speed2)]))


def get_temp():
    with open(ECIO_FILE, "rb") as ec:
        ec.seek(CPU_TEMP_OFFSET)
        temp_c = int.from_bytes(ec.read(1), "big")
        ec.seek(GPU_TEMP_OFFSET)
        temp_g = int.from_bytes(ec.read(1), "big")
    return max(temp_c, temp_g)


def bios_control(enabled):
    if enabled is False:
        with open(ECIO_FILE, "r+b") as ec:
            ec.seek(BIOS_OFFSET)
            ec.write(bytes([6]))
            sleep(0.1)
            ec.seek(TIMER_OFFSET)
            ec.write(bytes([0]))
    elif enabled is True:
        with open(ECIO_FILE, "r+b") as ec:
            ec.seek(BIOS_OFFSET)
            ec.write(bytes([0]))
            ec.seek(FAN1_OFFSET)
            ec.write(bytes([0]))
            ec.seek(FAN2_OFFSET)
            ec.write(bytes([0]))


signal.signal(signal.SIGTERM, sig_handler)
signal.signal(signal.SIGINT, sig_handler)

fd = os.open(IPC_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
os.write(fd, str(os.getpid()).encode())
os.close(fd)

BIOS_REFRESH_INTERVAL = 60  # seconds — EC timer is 120s, refresh at half that

HYSTERESIS = 2  # °C — ignore temp changes smaller than this

speed_old = -1
temp_old = -1
is_root()
log.info("starting (pid=%d, poll=%.1fs, curve=%s/%s)", os.getpid(), POLL_INTERVAL, TEMP_CURVE, SPEED_CURVE)
bios_control(False)

try:
    last_bios_refresh = time.monotonic()
    while True:
        temp = get_temp()

        # Skip small oscillations, but always respond if temp rises above last curve point
        if temp_old >= 0 and abs(temp - temp_old) < HYSTERESIS and temp < TEMP_CURVE[-1]:
            now = time.monotonic()
            if now - last_bios_refresh >= BIOS_REFRESH_INTERVAL:
                bios_control(False)
                last_bios_refresh = now
            sleep(POLL_INTERVAL)
            continue

        temp_old = temp

        if temp <= TEMP_CURVE[0]:
            speed = IDLE_SPEED
        elif temp >= TEMP_CURVE[-1]:
            speed = SPEED_CURVE[-1]
        else:
            i = bisect_left(TEMP_CURVE, temp)
            y0 = SPEED_CURVE[i - 1]
            x0 = TEMP_CURVE[i - 1]

            speed = y0 + slope[i - 1] * (temp - x0)

        if speed_old != speed:
            fan1 = int(FAN1_MAX * speed / 100)
            fan2 = int(FAN2_MAX * speed / 100)
            log.info("temp=%d°C speed=%.0f%% fan1=%d fan2=%d", temp, speed, fan1 * 100, fan2 * 100)
            speed_old = speed
            update_fan(FAN1_MAX * speed / 100, FAN2_MAX * speed / 100)

        now = time.monotonic()
        if now - last_bios_refresh >= BIOS_REFRESH_INTERVAL:
            bios_control(False)
            last_bios_refresh = now

        sleep(POLL_INTERVAL)
except Exception:
    log.exception("unhandled exception")
    raise
finally:
    log.info("restoring BIOS fan control")
    try:
        os.remove(IPC_FILE)
    except OSError:
        pass
    bios_control(True)
