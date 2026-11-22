# My kiln: hardware and settings for this fork

Settings for one specific kiln, carried over from the older `kilnController` fork (picoReflow lineage,
GitHub `botheredbybees/kilnController`; the local copy was deleted on 2026-11-22). They are kept here,
not in `config.py`, so `config.py` stays identical to upstream and pulls do not conflict. Apply them
to `config.py` **on the Pi**. `config.py` ships with `simulate = True`.

Values below come from the old repo's `README.md` and `config.py.EXAMPLE` and from the build guide
([PDF](Build-a-Web-Enabled-High-Temperature-Kiln-Controll.pdf), a copy of the
[Instructables guide](https://www.instructables.com/Build-a-Web-Enabled-High-Temperature-Kiln-Controll/), 2019).
The Pi's live `config.py` was not available when this was written. If it differs, trust the Pi.

## Hardware

- Second-hand pottery kiln, 10 A / 240 V (about 2.4 kW), rated to 1000 C. Door switch and a basic
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
terminal 2). The polarity is inferred: the guide drives the SSR input directly from the pin, so
high means on and `gpio_heat_invert = False`. The lamp test below checks it.

## Settings to change on the Pi

```python
simulate = False
max31855 = 1
max31856 = 0
temp_scale = "c"
time_scale_slope = "m"  # as in the old config
time_scale_profile = "m"  # as in the old config
kwh_rate = 0.28  # from the old config.py.EXAMPLE
currency_type = "AUD"
kw_elements = 2.4  # 10 A x 240 V, from the rating; measure it if you can
# 50 Hz mains. Only the MAX31856 code path reads this, so it does nothing with a MAX31855.
ac_freq_50hz = True
```

### Temperature limits: convert them, do not just flip `temp_scale`

Every temperature in `config.py` is read in the `temp_scale` you choose, and the shipped values are in
Fahrenheit. Setting `temp_scale = "c"` and leaving `emergency_shutoff_temp = 2264` turns the
over-temperature cutoff into 2264 C, which is effectively off.

| Setting | Shipped (F) | Suggested (C) | Note |
|---|---|---|---|
| `emergency_shutoff_temp` | 2264 | **1050, to confirm** | Trips at "reached or exceeded". The ported `bisque` and `glaze-1000c` profiles peak at exactly 1000 C, so it must be above 1000. The kiln is rated to 1000 C, so this is your decision. |
| `emergency_heat_rate` | 23 /h | 13 /h | Same physical rate. Check that the kiln can sustain it at its slowest ramp, or it will abort a good run. |
| `cooled_safe_temp` | 150 | 65 | |
| `relay_stuck_on_rise` | 25 | 14 | A temperature difference, so scale by 5/9 only. |
| `temp_implausible_jump` | 50 | 28 | Same. |
| `pid_control_window` | 10 | 6 | Must be a positive integer. |
| `throttle_below_temp` | 300 | 150 | Below this setpoint, and outside the PID window, heating is limited to `throttle_percent` (20 %). Left at 300 with `temp_scale = "c"` it would throttle up to 300 C, slowing the early ramp and risking a heat-rate abort. |

### Automatic restart

`automatic_restarts` is `True` by default. If the Pi loses power or reboots and comes back within
`automatic_restart_window` (15 minutes) of the last saved state, a running or paused firing **resumes by
itself**, with the elements switching on unattended. Decide on purpose whether you want that for this
kiln; `automatic_restarts = False` turns it off.

## PID is not tuned for this kiln

The shipped `pid_kp / pid_ki / pid_kd` are tuned for the simulator. The old repo's gains (0.5 / 0.1 /
0.4) use a different formulation (`pid_ki` is inverted here) and do not transfer. Tune with
`kiln-tuner.py` and `docs/pid_tuning.md` before any unattended firing.

## Profiles

`storage/profiles/` holds the ported profiles (`bisque`, `glass-fuse`, `glass-slump`, `glaze-1000c`),
all tagged `"temp_units": "c"`. A profile **without** that tag is read as Fahrenheit, so an untagged
copy of a Celsius profile would fire at the wrong temperature without any error.

Not ported: the old `glaze 1030c`, which peaks at 1067 C, above the kiln's 1000 C rating. Its data is
kept here so it is not lost (seconds, degrees C, in the old file's format):

```json
{"name": "glaze 1030c", "data": [[0, 30], [2786, 159], [6002, 155], [23966, 999], [30011, 1063], [34427, 1067], [39919, 1017]]}
```

To use it, rename it to match `^[A-Za-z0-9._-]+$`, add `"temp_units": "c"`, and lower the peak to what
the kiln can take.

## Commissioning with a lamp

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

## Not verified

Nothing here has been run against the real kiln. The simulator starts and serves the UI on a laptop;
the relay, thermocouple and the emergency limits have not been exercised on hardware.
