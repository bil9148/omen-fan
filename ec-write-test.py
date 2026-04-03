#!/usr/bin/env python3
"""
Minimal EC write test for HP Omen 15-en1xxx (board 88D2).
Tests whether writing to 0x34/0x35 controls fan speed.

Flow:
  1. Read baseline from hp-wmi (safe)
  2. Disable BIOS control (0x62=6, 0x63=0)
  3. Write current approximate speed to 0x34/0x35 (should change nothing)
  4. Wait, verify fans still running via hp-wmi
  5. Write a slightly different speed, check if hp-wmi reading changes
  6. Re-enable BIOS control (0x62=0)
"""

import os
import sys
import glob
import time
import subprocess

ECIO_FILE = "/sys/kernel/debug/ec/ec0/io"

FAN1_SET = 0x34
FAN2_SET = 0x35
BIOS_CTL = 0x62
TIMER = 0x63
FAN1_PCT = 0x2E
FAN2_PCT = 0x2F

FAN1_HWMON = glob.glob("/sys/devices/platform/hp-wmi/hwmon/*/fan1_input")[0]
FAN2_HWMON = glob.glob("/sys/devices/platform/hp-wmi/hwmon/*/fan2_input")[0]


def read_ec(ec, offset):
    ec.seek(offset)
    return int.from_bytes(ec.read(1), "big")


def write_ec(ec, offset, value):
    ec.seek(offset)
    ec.write(bytes([value]))
    ec.flush()


def read_hwmon(path):
    with open(path, "r") as f:
        return int(f.read().strip())


def enable_bios_control(ec):
    write_ec(ec, BIOS_CTL, 0)
    write_ec(ec, FAN1_SET, 0)
    write_ec(ec, FAN2_SET, 0)
    print("  BIOS control re-enabled.")


def main():
    if os.geteuid() != 0:
        print("ERROR: Need root.")
        sys.exit(1)

    if "ec_sys" not in subprocess.check_output(["lsmod"]).decode():
        subprocess.run(["modprobe", "ec_sys", "write_support=1"], check=True)

    # Check write support
    if not bool(os.stat(ECIO_FILE).st_mode & 0o200):
        subprocess.run(["modprobe", "-r", "ec_sys"], check=True)
        subprocess.run(["modprobe", "ec_sys", "write_support=1"], check=True)

    print("=" * 60)
    print("  EC Write Test — HP Omen 15-en1xxx")
    print("=" * 60)

    # Step 1: baseline
    rpm1 = read_hwmon(FAN1_HWMON)
    rpm2 = read_hwmon(FAN2_HWMON)
    approx1 = max(1, round(rpm1 / 100))  # convert RPM to EC units
    approx2 = max(1, round(rpm2 / 100))
    print(f"\n  Baseline (hp-wmi): Fan1={rpm1} RPM, Fan2={rpm2} RPM")
    print(f"  Will write: Fan1={approx1} (≈{approx1*100} RPM), Fan2={approx2} (≈{approx2*100} RPM)")

    with open(ECIO_FILE, "r+b", buffering=0) as ec:
        pct1_before = read_ec(ec, FAN1_PCT)
        pct2_before = read_ec(ec, FAN2_PCT)
        print(f"  EC fan %: Fan1={pct1_before}%, Fan2={pct2_before}%")

        try:
            # Step 2: disable BIOS control
            print("\n  [1/4] Disabling BIOS control...")
            write_ec(ec, BIOS_CTL, 6)
            time.sleep(0.1)
            write_ec(ec, TIMER, 0)
            bios_val = read_ec(ec, BIOS_CTL)
            print(f"         BIOS_CTL=0x{bios_val:02X} ({'OK — disabled' if bios_val == 6 else 'UNEXPECTED'})")

            if bios_val != 6:
                print("  ABORT: BIOS control register didn't accept write.")
                enable_bios_control(ec)
                sys.exit(1)

            # Step 3: write current speed (should not change fan behavior)
            print(f"\n  [2/4] Writing current speed ({approx1}/{approx2})...")
            write_ec(ec, FAN1_SET, approx1)
            write_ec(ec, FAN2_SET, approx2)
            time.sleep(2)

            rpm1_after = read_hwmon(FAN1_HWMON)
            rpm2_after = read_hwmon(FAN2_HWMON)
            pct1_after = read_ec(ec, FAN1_PCT)
            set1_after = read_ec(ec, FAN1_SET)
            set2_after = read_ec(ec, FAN2_SET)
            print(f"         hp-wmi: Fan1={rpm1_after} RPM, Fan2={rpm2_after} RPM")
            print(f"         EC 0x2E={pct1_after}%, EC 0x34={set1_after}, EC 0x35={set2_after}")
            print(f"         Fan1 delta from baseline: {abs(rpm1_after - rpm1)} RPM")

            # Check if 0x34 accepted the value (no longer 0xFF)
            if set1_after == approx1:
                print("         0x34 accepted write value — register is writable!")
            elif set1_after == 0xFF:
                print("         0x34 still 0xFF — register may not work on this model")

            # Step 4: write a noticeably lower speed to see if fans respond
            lower1 = max(1, approx1 - 10)
            lower2 = max(1, approx2 - 10)
            print(f"\n  [3/4] Writing lower speed ({lower1}/{lower2}) to test response...")
            write_ec(ec, FAN1_SET, lower1)
            write_ec(ec, FAN2_SET, lower2)
            time.sleep(4)  # fans take time to spin down

            rpm1_lower = read_hwmon(FAN1_HWMON)
            rpm2_lower = read_hwmon(FAN2_HWMON)
            pct1_lower = read_ec(ec, FAN1_PCT)
            print(f"         hp-wmi: Fan1={rpm1_lower} RPM, Fan2={rpm2_lower} RPM")
            print(f"         EC 0x2E={pct1_lower}%")
            print(f"         Fan1 change: {rpm1_after} → {rpm1_lower} RPM (delta={rpm1_after - rpm1_lower})")

            # Step 5: re-enable BIOS
            print(f"\n  [4/4] Re-enabling BIOS control...")
            enable_bios_control(ec)
            time.sleep(2)

            bios_final = read_ec(ec, BIOS_CTL)
            rpm1_final = read_hwmon(FAN1_HWMON)
            rpm2_final = read_hwmon(FAN2_HWMON)
            print(f"         BIOS_CTL=0x{bios_final:02X} ({'OK' if bios_final == 0 else 'UNEXPECTED'})")
            print(f"         hp-wmi: Fan1={rpm1_final} RPM, Fan2={rpm2_final} RPM")

        except Exception as e:
            print(f"\n  EXCEPTION: {e}")
            print("  Re-enabling BIOS control...")
            enable_bios_control(ec)
            raise

    # Verdict
    print("\n" + "=" * 60)
    fan_responded = abs(rpm1_after - rpm1_lower) > 200
    bios_accepted = bios_val == 6
    bios_restored = bios_final == 0

    if fan_responded and bios_accepted and bios_restored:
        print("  RESULT: SUCCESS")
        print("  - BIOS control toggle works (0x62)")
        print("  - Fan speed responds to 0x34/0x35 writes")
        print("  - BIOS control restored cleanly")
        print("  → omen-fan is COMPATIBLE with your laptop.")
    elif bios_accepted and bios_restored and not fan_responded:
        print("  RESULT: PARTIAL")
        print("  - BIOS control toggle works")
        print("  - Fan speed did NOT clearly respond to writes")
        print("  - Could be: fans adjusting slowly, or 0x34/0x35 wrong for this model")
        print("  → Compatibility uncertain. Needs more investigation.")
    else:
        print("  RESULT: INCOMPATIBLE")
        print(f"  - BIOS control: {'OK' if bios_accepted else 'FAILED'}")
        print(f"  - BIOS restore: {'OK' if bios_restored else 'FAILED'}")
        print(f"  - Fan response: {'OK' if fan_responded else 'NONE'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
