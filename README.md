# omen-fan

Manual fan curve control for HP Omen laptops on Linux via EC (Embedded Controller) register writes.

Fork of [alou-S/omen-fan](https://github.com/alou-S/omen-fan) with security hardening, crash recovery, logging, and hysteresis.

## Supported Models

- HP Omen 16-c0xxx (original, tested by alou-S)
- HP Omen 15-en1xxx (verified — EC registers confirmed compatible)
- Other HP Omen/Victus models may work — see [Compatibility](#compatibility)

## Why Not hp-wmi?

The kernel's `hp-wmi` driver only exposes three coarse thermal profiles (cool/balanced/performance) and a boost toggle on older Omen models. There is no `pwm1` interface for setting arbitrary fan speeds. Kernel 7.0 adds PWM support, but only for newer "Victus S" board IDs. For older Omens, raw EC writes are the only path to custom fan curves.

## Changes from upstream

- **Security:** PID file moved from `/tmp/` to `/run/` (prevents symlink attacks as root)
- **Security:** Daemon launched with absolute path + `sys.executable` (prevents PATH hijacking)
- **Reliability:** `try/finally` crash recovery — BIOS fan control is always restored on any exit (exception, SIGTERM, SIGINT)
- **Reliability:** Config validation in daemon — rejects bad curves, out-of-range values, zero poll intervals
- **Reliability:** Graceful error on missing `hp-wmi` module instead of `IndexError` crash
- **Performance:** BIOS control refresh every 60s instead of every 1s (EC timer is 120s)
- **Logging:** Syslog output via `journalctl -t omen-fand` — startup, fan changes, errors, shutdown
- **Hysteresis:** 2-degree deadband prevents fan speed oscillation on small temp fluctuations
- **Bug fix:** Device check logic (`any()` -> `all()`) — was broken with multiple entries in device list

## Requirements

- Arch Linux (or any distro with `ec_sys` module support)
- Python 3
- `python-click`, `python-tomlkit`, `python-click-aliases`
- `hp-wmi` kernel module (loaded by default on HP laptops)

### Arch Linux

```bash
sudo pacman -S python-click python-tomlkit python-click-aliases
```

## Usage

```bash
# Set fan speed manually (disables BIOS control)
sudo python3 omen-fan.py set 50%
sudo python3 omen-fan.py set 40 45       # fan1=4000 RPM, fan2=4500 RPM

# Start the fan curve daemon
sudo python3 omen-fan.py service start

# Stop daemon (re-enables BIOS control)
sudo python3 omen-fan.py service stop

# View current config
sudo python3 omen-fan.py configure --view

# Set a custom curve
sudo python3 omen-fan.py configure \
  --temp-curve 40,50,60,70,80,90 \
  --speed-curve 25,40,60,75,90,100 \
  --idle-speed 15

# Toggle boost mode (max fans)
sudo python3 omen-fan.py boost true

# Re-enable BIOS fan control
sudo python3 omen-fan.py bios-control true

# Check fan status
python3 omen-fan.py info
```

### Monitor logs

```bash
journalctl -t omen-fand -f
```

## Configuration

Config lives at `/etc/omen-fan/config.toml` (created on first run):

```toml
[service]
TEMP_CURVE = [40, 50, 60, 70, 80, 90]    # temperature breakpoints (C)
SPEED_CURVE = [25, 40, 60, 75, 90, 100]  # fan speed at each point (%)
IDLE_SPEED = 15                            # fan speed below lowest temp
POLL_INTERVAL = 1                          # seconds between temp checks

[script]
BYPASS_DEVICE_CHECK = 0                    # set to 1 to skip model validation
```

Fan speed is linearly interpolated between curve points. Restart the daemon after changing config.

## Compatibility

This tool writes directly to EC registers. The register layout is model-specific. Before using on an untested model:

1. Run the read-only probe to verify registers match:
   ```bash
   sudo python3 ec-probe.py
   ```
2. If registers look correct, run the write test:
   ```bash
   sudo python3 ec-write-test.py
   ```

Both scripts are included in this repo. A reboot resets all EC registers if anything goes wrong.

### EC Register Map

| Register | Function |
|----------|----------|
| `0x34` | Fan 1 speed set (units of 100 RPM, max 55) |
| `0x35` | Fan 2 speed set (units of 100 RPM, max 57) |
| `0x2E` | Fan 1 speed % (read-only) |
| `0x2F` | Fan 2 speed % (read-only) |
| `0x57` | CPU temperature (read-only) |
| `0xB7` | GPU temperature (read-only) |
| `0x62` | BIOS fan control (0=enabled, 6=disabled) |
| `0x63` | Timer (counts down, resets BIOS control at 0) |

## WARNING

Forcing this program to run on incompatible laptops may cause hardware damage. The EC register layout varies between models. Always verify with `ec-probe.py` first. A reboot will reset all EC registers to factory defaults.

## License

GPLv3 — see [LICENSE](LICENSE).

## Credits

Original project by [alou-S](https://github.com/alou-S/omen-fan).
