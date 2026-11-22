# My kiln: hardware and settings for this fork

Settings for one specific kiln, carried over from the older `kilnController` fork (picoReflow lineage,
GitHub `botheredbybees/kilnController`; the local copy was deleted on 2026-11-22). They are kept here,
not in `config.py`, so `config.py` stays identical to upstream and pulls do not conflict. Apply them
to `config.py` **on the Pi**. `config.py` ships with `simulate = True`.

Values below come from the old repo's `README.md` and `config.py.EXAMPLE` and from the build guide
([PDF](Build-a-Web-Enabled-High-Temperature-Kiln-Controll.pdf), a copy of the
[Instructables guide](https://www.instructables.com/Build-a-Web-Enabled-High-Temperature-Kiln-Controll/), 2019).
The Pi's live configuration was read from its SD card on 2026-11-22 (see "What the Pi actually runs"
below), and where it differs from the 2019 values in this file, the live values win.

## Hardware

- A very old enamelling kiln pressed into service for pottery, 10 A / 240 V (`kw_elements = 2.00` in
  the Pi's live config). It reaches about 1000 C at best, perhaps 1050 C on a warm day (owner, 2026-11-22),
  so the highest profile peaks (1063 C and 1020 C) are at or beyond what it can do. Door switch and a basic
  thermostat (25 / 50 / 75 / 100 % power), no timer. Neither the door switch nor the thermostat is
  read by the controller.
- Raspberry Pi Zero W (Raspbian in the 2019 build), Adafruit MAX31855 with a K-type thermocouple,
  SSR-25DA solid-state relay.
- The SSR switches the active wire of a short extension cord that the kiln plugs into (build guide,
  step 5). The kiln is in Tasmania, so mains is 50 Hz.

## Wiring to config names

The old wiring (README) is software SPI on GPIO 17 / 27 / 22. **The upstream defaults in `config.py`
use a different assignment** (clock 17, miso 27, cs 22), so copying the defaults would read the
thermocouple on the wrong pins.

| MAX31855 pin | Pi pin | BCM | `config.py` setting |
|---|---|---|---|
| DO | 11 | GPIO17 | `spi_miso = board.D17` |
| CS | 13 | GPIO27 | `spi_cs = board.D27` |
| CLK | 15 | GPIO22 | `spi_sclk = board.D22` |
| 3Vo | 1 | 3.3 V | not configured |
| GND | 9 | ground | not configured |
| (not wired) | | GPIO10 | `spi_mosi = board.D10` |
| SSR `+` (terminal 3) | 16 | GPIO23 | `gpio_heat = board.D23`, `gpio_heat_invert = False` |
| SSR ground (terminal 2) | 6 | ground | not configured |

The SSR pin is confirmed by the build guide (step 5: Pi pin 16 to the SSR `+` terminal, pin 6 to
terminal 2) **and by the Pi's own live config** (`gpio_heat = 23`, thermocouple data/chip-select/clock
on 17/27/22). The polarity is confirmed: the running code drives the pin HIGH for heat and LOW for off,
and on 2026-11-22 GPIO 23 was read high while heat was commanded and low after Stop (see the hardware
test below), so `gpio_heat_invert = False`.

## Settings changed on the Pi (as deployed 2026-11-22)

These are the values the Pi's `config.py` has now, on top of the shipped file. Everything not listed
(including the shipped hour-based `time_scale_*` settings, and the old config's minute scales, which were
not applied) is as shipped.

```python
simulate = False
max31855 = 1
max31856 = 0
temp_scale = "c"
listening_port = 8081
kwh_rate = 0.17          # from the Pi's old live config (the 2019 example said 0.28)
kw_elements = 2.00
# 50 Hz mains. Only the MAX31856 code path reads this, so it does nothing with a MAX31855.
ac_freq_50hz = True
spi_sclk = board.D22
spi_miso = board.D17
spi_cs = board.D27
temperature_average_samples = 40
automatic_restarts = False
sim_t_env = 18          # only used when simulating
```

### Temperature limits: convert them, do not just flip `temp_scale`

Every temperature in `config.py` is read in the `temp_scale` you choose, and the shipped values are in
Fahrenheit. Setting `temp_scale = "c"` and leaving `emergency_shutoff_temp = 2264` turns the
over-temperature cutoff into 2264 C, which is effectively off.

| Setting | Shipped (F) | Suggested (C) | Note |
|---|---|---|---|
| `emergency_shutoff_temp` | 2264 | **1100** | Trips at "reached or exceeded". The kiln tops out at about 1000 to 1050 C, so 1100 C is about 50 C beyond its best day and still catches a real runaway, while leaving about 40 C above the highest profile peak (1063 C) so normal overshoot cannot trip it. The old Pi's live value was 1200 C, about 150 C above anything the kiln can reach. |
| `emergency_heat_rate` | 23 /h | 13 /h | Same physical rate. Check that the kiln can sustain it at its slowest ramp, or it will abort a good run. |
| `cooled_safe_temp` | 150 | 65 | |
| `relay_stuck_on_rise` | 25 | 14 | A temperature difference, so scale by 5/9 only. |
| `temp_implausible_jump` | 50 | 28 | Same. |
| `pid_control_window` | 10 | 6 | Must be a positive integer. |
| `throttle_below_temp` | 300 | 150 | Below this setpoint, and outside the PID window, heating is limited to `throttle_percent` (20 %). Left at 300 with `temp_scale = "c"` it would throttle up to 300 C, slowing the early ramp and risking a heat-rate abort. |

Deployed values differ from the suggestions in three rows: `emergency_heat_rate = 5`,
`pid_control_window = 5` and `temp_implausible_jump = 10`. `emergency_heat_rate` is a **minimum** heating
rate: it aborts a firing that fails to heat at least that fast over `emergency_heat_rate_window` minutes
while the profile is rising (a dead element or a relay stuck open). It is not a limit on how fast the kiln
may heat. 5 per hour is far below what this kiln does (hundreds per hour at full power), so it only catches
a kiln that is barely heating. The tuner does not run the oven's emergency checks, so none of these limits
has been exercised on hardware yet.

### Automatic restart

`automatic_restarts` is `True` by default. If the Pi loses power or reboots and comes back within
`automatic_restart_window` (15 minutes) of the last saved state, a running or paused firing **resumes by
itself**, with the elements switching on unattended. Decide on purpose whether you want that for this
kiln; `automatic_restarts = False` turns it off. **It is off on this kiln** (deployed 2026-11-22), so the controller does not come back by itself after a power cut, and the service is not enabled at boot.

## PID: provisionally tuned, not yet proven in a firing

The shipped `pid_kp / pid_ki / pid_kd` (4.112 / 32.972 / 139.384) are tuned for the simulator, and the old
repo's gains (0.5 / 0.1 / 0.4) use a different formulation (`pid_ki` is inverted here: `iterm += err * dt *
(1/pid_ki)`, so a **larger `pid_ki` is a weaker integral**) and do not transfer.

On 2026-11-22 the built-in tuner (`/api/tune`, see `docs/pid_tuning.md`) ran to 187 C and wrote
**`pid_kp = 1.653`, `pid_ki = 259.801`, `pid_kd = 177.536`** (dead time 107 s, time constant 296 s,
critically damped rule). Against the shipped values that is a gentler proportional term, a much weaker
integral and a slightly stronger derivative. Treat them as provisional: the run began while the kiln was
still coasting upward after an earlier burst, which probably inflates the measured dead time, and a
cold-start run would be cleaner. Nobody has yet seen how they track a real profile; check a short, watched
low-temperature firing before any unattended one. The shipped values are saved in the Pi's
`config.py.pre-tune` if you want them back.

## Profiles

`storage/profiles/` holds the ported profiles (`bisque`, `glass-fuse`, `glass-slump`, `glaze-1000c`),
all tagged `"temp_units": "c"`. (The Pi itself holds all 14 of the old kiln's profiles, converted the same way: tagged `"c"`, names made safe, and the two same-named pairs separated.) A profile **without** that tag is read as Fahrenheit, so an untagged
copy of a Celsius profile would fire at the wrong temperature without any error.

Not ported: the old `glaze 1030c`, which peaks at 1067 C, above the kiln's 1000 C rating. Its data is
kept here so it is not lost (seconds, degrees C, in the old file's format):

```json
{"name": "glaze 1030c", "data": [[0, 30], [2786, 159], [6002, 155], [23966, 999], [30011, 1063], [34427, 1067], [39919, 1017]]}
```

To use it, rename it to match `^[A-Za-z0-9._-]+$`, add `"temp_units": "c"`, and lower the peak to what
the kiln can take.

## Commissioning with a lamp

This was written as the way to check the pin and polarity under new software. Both are now confirmed
directly on the Pi (see "Hardware test"), so it is only needed again if the wiring changes.
Adapted from the build guide, step 6. Do this before the kiln is plugged into the controller:

1. Plug a desk lamp into the controller instead of the kiln, and rest the thermocouple on the bulb.
   With the lamp off, the controller should see room temperature.
2. Start a profile whose first target is above room temperature (the ported profiles start at 20 to
   30 C; in a cold room the first target is already above ambient). The lamp should come on and
   then cycle as the controller tracks the profile.
3. If the lamp is on when it should be off, the relay polarity is wrong: set `gpio_heat_invert = True`.
4. Stop the run from the web interface and check the lamp goes off at once.

This exercises the thermocouple wiring, the SSR pin and the polarity. It does not exercise the
emergency limits or the PID tuning.

## What the Pi actually runs

**Now (new card, 2026-11-22):** Raspberry Pi OS Lite (Bookworm, 32-bit, Python 3.11.2) on a SanDisk 64 GB
card, hostname `kiln-rpi`, page at `http://kiln-rpi.local:8081/` (the address comes from the router's DHCP).
`/home/pi/kiln-controller` is the fork's `integration/fixes` build (the fix branches merged), with
`simulate = False`. It is started by hand with `/home/pi/kiln-start.sh` and stopped with
`/home/pi/kiln-stop.sh` (pid file and port check). The systemd unit is staged as
`kiln-controller.service.pending` but **not installed or enabled**. An earlier spare 32 GB card was
rejected because it showed filesystem corruption within minutes of first boot; the card was built with a
write-then-read-back check of the image.

**The old card (fallback, untouched):** Raspbian 11 (Bullseye), Python 3.9.2, **upstream
`jbruce12000/kiln-controller` at commit `7c7a1b6` (2022-12-16)**, started by `kiln-controller.service`. Python
3.9 cannot run this fork (it needs 3.11 or newer). Its settings differ from upstream's `config.py`:
`simulate = False`, `temp_scale = "c"`, `emergency_shutoff_temp = 1200` (too high for this kiln; the new install
uses 1100), `kw_elements = 2.00`, `kwh_rate = 0.17`, `ac_freq_50hz = True`, PID `kp 25, ki 10, kd 200` (that
version's formulation), `automatic_restarts = True` with a 15 minute window. Its 14 profiles (peaks 94 to
1063 C) carry **no `temp_units` tag**: they are Celsius, so copying them into this fork unchanged would make
them read as Fahrenheit (a 715 C profile would run at about 379 C). A copy of its profiles, live `config.py`,
the diff of the local changes and the last saved state was taken before the card was edited (kept on the
laptop, outside this repo).

## Hardware tests, 2026-11-22

### First heating (old card, the Pi's existing upstream controller)

The kiln had not been run for over a year. With it empty and the owner present at the wall switch, the
Pi's **existing** controller (not this fork) was used for a supervised first heating:

- **Pin and polarity:** GPIO 23 read high (four reads) while the controller commanded full heat and low
  (three reads) after Stop, read directly on the Pi with `raspi-gpio`.
- **Heating:** the existing `Preheat` profile took the kiln from 20.1 C to 28.5 C in 98 s at full power.
  Stop returned the controller to IDLE with heat 0 within about 2 s. The reading then kept rising to a
  peak of 60.2 C about 6 to 7 minutes after the stop, stayed flat for a few minutes and then fell about 1 C
  a minute with the controller idle. The thermocouple sits away from the elements, so a 100 s full-power run
  coasts by about 30 C: allow for that when choosing stop rules and ceilings for any test.
- **Power:** `vcgencmd get_throttled` read `0x0` (no undervoltage) before, during and after.
- **An anomaly:** mid-run the reading dropped for a few seconds (22.5, 18.1, 2.9, 19.9, 24.0 C), which
  the kiln cannot physically do. The cause is not established. Software-SPI reads can be corrupted when
  the Pi's CPU is busy (a login over SSH was running at about that time), and electrical noise from the
  relay is another candidate. A heater-off check did not support the CPU theory: 44 idle readings and 41
  readings during an SSH login plus a 25 s CPU burn had no jumps. The heated run should be repeated with
  nothing else running on the Pi before the sensor is trusted for a long firing.
- **Not tested then:** this fork's code on the kiln, the emergency limits, and anything above about 35 C.

### Tuning runs (new card, the fork's integration build)

Kiln empty, owner within reach of the wall switch, a laptop monitor logging every reading and sending Stop
on its own rules. The kiln is small and light with a poorly insulated door (owner), so it heats and cools
fast.

- **Run 1, cold start (29.3 C):** stopped by the monitor after 151 s at full power because its rate rule
  (15 C/min) was set from a wrong estimate. Measured rate in successive 30 s windows: 0.5, 3.5, 8.3, 13.3,
  16.9 C/min, still accelerating. With the heater off the reading kept rising from 51.5 to **87.0 C
  (+35.5 C)**, then decayed slowly (about 1 C/min near 60 C). Lesson: set stop rules from measured data.
- **Run 2, warm start (86.8 C), tuner to 187 C:** heater on 342 s, off at 191.8 C, coast peak **212.9 C**
  (+21 C, 2.5 min later). Falling about 5 C/min near 190 to 200 C; 200 C at 3.7 min after the peak, 180 C at
  7.7 min, 150 C at 15.3 min. The tuner then calculated and applied the PID values given above, and the
  new values were active in the running controller at once.
- **Heater state:** GPIO 23 read low and the controller IDLE with heat 0 after each run.
- **Sensor:** across both runs 247 heated readings showed a largest step of 1.03 C between readings and no
  jump of 3 C or more, so the glitch above was not seen again (not ruled out: the software, the OS and
  40-sample averaging differ from the first test).
- **Not tested:** any firing profile; the oven's emergency limits (the tuner skips them); anything above
  213 C; how the tuned PID tracks a real ramp. The tuner does not save its raw data, so a laptop script logged
  the live status feed.

## Not verified

The fix branches in this fork have been run in the test suite and in simulation. On the kiln, only the
integration build's tuner has run (full power to 213 C, under supervision). No profile has been fired, and the
emergency limits (over-temperature, heat rate, stuck relay, stale reading) have not been exercised on hardware.
