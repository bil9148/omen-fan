#!/usr/bin/env python3
"""
Read-only EC register probe for HP Omen laptops.
Compares EC register values against known-safe hp-wmi readings
to verify whether omen-fan's hardcoded offsets are correct for this model.

Does NOT write to any registers. Safe to run.
Requires: sudo modprobe ec_sys (write_support not needed for reads)
"""

import os
import sys
import time

ECIO_FILE = "/sys/kernel/debug/ec/ec0/io"

# Offsets from omen-fan (probed on Omen 16-c0xxx)
OFFSETS = {
    0x34: "Fan 1 Speed Set (units of 100 RPM)",
    0x35: "Fan 2 Speed Set (units of 100 RPM)",
    0x2E: "Fan 1 Speed % (0-100)",
    0x2F: "Fan 2 Speed % (0-100)",
    0xB1: "Fan 1 Speed (0-22 range)",
    0xB3: "Fan 2 Speed (0-22 range)",
    0x57: "CPU Temp (°C)",
    0xB7: "GPU Temp (°C)",
    0x62: "BIOS Control (0=enabled, 6=disabled)",
    0x63: "Timer (countdown to BIOS reset)",
    0x95: "Performance Mode",
    0xEC: "Fan Boost (0=off, 0x0C=on)",
    0xF4: "Fan State (0=enable, 2=disable)",
}

# Safe hp-wmi paths for cross-reference
HWMON_FAN1 = "/sys/devices/platform/hp-wmi/hwmon/hwmon*/fan1_input"
HWMON_FAN2 = "/sys/devices/platform/hp-wmi/hwmon/hwmon*/fan2_input"
HWMON_BOOST = "/sys/devices/platform/hp-wmi/hwmon/hwmon*/pwm1_enable"
THERMAL_ZONE_PATTERN = "/sys/class/thermal/thermal_zone*/temp"


def find_file(pattern):
    """Resolve a glob pattern to a single file."""
    import glob
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def read_ec_byte(ec_file, offset):
    """Read a single byte from EC at the given offset."""
    ec_file.seek(offset)
    return int.from_bytes(ec_file.read(1), "big")


def read_sysfs(path):
    """Read an integer from a sysfs file."""
    if path and os.path.exists(path):
        with open(path, "r") as f:
            return f.read().strip()
    return "N/A"


def get_thermal_zones():
    """Read all thermal zone temperatures for cross-reference."""
    import glob
    zones = {}
    for path in sorted(glob.glob(THERMAL_ZONE_PATTERN)):
        zone_dir = os.path.dirname(path)
        type_path = os.path.join(zone_dir, "type")
        zone_type = read_sysfs(type_path)
        temp = read_sysfs(path)
        if temp != "N/A":
            zones[zone_type] = int(temp) / 1000  # millidegrees to degrees
    return zones


def main():
    if os.geteuid() != 0:
        print("ERROR: Root required to read EC registers.")
        print("Usage: sudo python3 ec-probe.py")
        sys.exit(1)

    # Ensure ec_sys is loaded (read-only is fine)
    import subprocess
    if "ec_sys" not in subprocess.check_output(["lsmod"]).decode():
        print("Loading ec_sys module (read-only)...")
        subprocess.run(["modprobe", "ec_sys"], check=True)

    if not os.path.exists(ECIO_FILE):
        print(f"ERROR: {ECIO_FILE} not found. ec_sys module may not be loaded.")
        sys.exit(1)

    # Read hp-wmi values (safe/known-good)
    fan1_file = find_file(HWMON_FAN1)
    fan2_file = find_file(HWMON_FAN2)
    boost_file = find_file(HWMON_BOOST)

    hwmon_fan1 = read_sysfs(fan1_file)
    hwmon_fan2 = read_sysfs(fan2_file)
    hwmon_boost = read_sysfs(boost_file)
    thermal_zones = get_thermal_zones()

    print("=" * 62)
    print("  EC Register Probe — Read Only")
    print(f"  Model: {read_sysfs('/sys/devices/virtual/dmi/id/product_name')}")
    print(f"  Board: {read_sysfs('/sys/devices/virtual/dmi/id/board_name')}")
    print("=" * 62)

    # Read all EC registers
    print("\n  EC Register Dump:")
    print("  " + "-" * 58)
    with open(ECIO_FILE, "rb") as ec:
        for offset, desc in sorted(OFFSETS.items()):
            val = read_ec_byte(ec, offset)
            print(f"  0x{offset:02X} = {val:>3d} (0x{val:02X})  {desc}")
    print("  " + "-" * 58)

    # Cross-reference section
    print("\n  Cross-Reference (EC vs hp-wmi / thermal zones):")
    print("  " + "-" * 58)

    with open(ECIO_FILE, "rb") as ec:
        ec_fan1_set = read_ec_byte(ec, 0x34)
        ec_fan1_pct = read_ec_byte(ec, 0x2E)
        ec_fan2_set = read_ec_byte(ec, 0x35)
        ec_fan2_pct = read_ec_byte(ec, 0x2F)
        ec_cpu_temp = read_ec_byte(ec, 0x57)
        ec_gpu_temp = read_ec_byte(ec, 0xB7)
        ec_bios_ctl = read_ec_byte(ec, 0x62)
        ec_boost = read_ec_byte(ec, 0xEC)

    print(f"  Fan 1:  hp-wmi={hwmon_fan1} RPM,  EC 0x34={ec_fan1_set} (×100={ec_fan1_set*100} RPM),  EC 0x2E={ec_fan1_pct}%")
    print(f"  Fan 2:  hp-wmi={hwmon_fan2} RPM,  EC 0x35={ec_fan2_set} (×100={ec_fan2_set*100} RPM),  EC 0x2F={ec_fan2_pct}%")

    # Check if EC fan values correlate with hp-wmi
    if hwmon_fan1 != "N/A" and ec_fan1_set > 0:
        hwmon_val = int(hwmon_fan1)
        ec_val = ec_fan1_set * 100
        delta = abs(hwmon_val - ec_val)
        match = "MATCH" if delta < 500 else "MISMATCH"
        print(f"  Fan 1 correlation: delta={delta} RPM  → {match}")

    if hwmon_fan2 != "N/A" and ec_fan2_set > 0:
        hwmon_val = int(hwmon_fan2)
        ec_val = ec_fan2_set * 100
        delta = abs(hwmon_val - ec_val)
        match = "MATCH" if delta < 500 else "MISMATCH"
        print(f"  Fan 2 correlation: delta={delta} RPM  → {match}")

    print(f"\n  CPU Temp:  EC 0x57={ec_cpu_temp}°C")
    print(f"  GPU Temp:  EC 0xB7={ec_gpu_temp}°C")
    print(f"  Thermal zones:")
    for name, temp in thermal_zones.items():
        label = ""
        if abs(temp - ec_cpu_temp) < 5:
            label = " ← likely matches EC CPU temp"
        elif abs(temp - ec_gpu_temp) < 5:
            label = " ← likely matches EC GPU temp"
        print(f"    {name:>20s} = {temp:.1f}°C{label}")

    print(f"\n  BIOS Control: EC 0x62={ec_bios_ctl} ({'DISABLED' if ec_bios_ctl == 6 else 'ENABLED' if ec_bios_ctl == 0 else 'UNKNOWN'})")
    print(f"  Fan Boost:    EC 0xEC={ec_boost} (hp-wmi pwm1_enable={hwmon_boost})")

    # Verdict
    print("\n" + "=" * 62)
    issues = []
    if hwmon_fan1 != "N/A" and ec_fan1_set > 0:
        if abs(int(hwmon_fan1) - ec_fan1_set * 100) >= 500:
            issues.append("Fan 1 EC value doesn't correlate with hp-wmi reading")
    if hwmon_fan2 != "N/A" and ec_fan2_set > 0:
        if abs(int(hwmon_fan2) - ec_fan2_set * 100) >= 500:
            issues.append("Fan 2 EC value doesn't correlate with hp-wmi reading")
    if ec_cpu_temp == 0 or ec_cpu_temp > 110:
        issues.append(f"CPU temp from EC looks wrong: {ec_cpu_temp}°C")
    if ec_gpu_temp > 110:
        issues.append(f"GPU temp from EC looks wrong: {ec_gpu_temp}°C")
    if ec_bios_ctl not in (0, 6):
        issues.append(f"BIOS control register has unexpected value: {ec_bios_ctl}")

    if not issues:
        print("  VERDICT: EC registers appear consistent with omen-fan offsets.")
        print("  The register mappings likely match your hardware.")
    else:
        print("  VERDICT: Potential issues found:")
        for issue in issues:
            print(f"    ⚠ {issue}")
        print("  Proceed with caution — registers may differ on this model.")
    print("=" * 62)

    # Second sample for delta check
    print("\n  Taking second reading in 3 seconds for drift check...")
    time.sleep(3)

    with open(ECIO_FILE, "rb") as ec:
        ec_fan1_set2 = read_ec_byte(ec, 0x34)
        ec_fan2_set2 = read_ec_byte(ec, 0x35)
        ec_cpu_temp2 = read_ec_byte(ec, 0x57)

    hwmon_fan1_2 = read_sysfs(fan1_file)

    print(f"  Fan 1: EC={ec_fan1_set2}→{ec_fan1_set2*100} RPM, hp-wmi={hwmon_fan1_2} RPM")
    print(f"  CPU Temp: {ec_cpu_temp}°C → {ec_cpu_temp2}°C")
    print("  (Values should track together if registers are correct)")
    print()


if __name__ == "__main__":
    main()
