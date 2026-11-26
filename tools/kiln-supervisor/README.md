# Kiln supervisor tools

Small laptop-side scripts for supervising a firing on the kiln's Pi. They talk to the controller's web
API and status websocket only (no ssh). Written during the first real firings of this fork on a small
second-hand kiln (about 2.4 kW, tops out near 865 C); the numbers in the rules come from those firings.

**These are not a safety device.** A Stop sent over the network cannot reach a Pi that has dropped off
it, and cannot open a solid-state relay that has welded shut. The first heated test lost its network
mid-run and no Stop could be delivered. Safety belongs on the Pi (watchdog, `emergency_shutoff_temp`) and
in the kiln circuit (an independent over-temperature cut-out), with a person near the wall switch.
These tools add a second opinion and a record, nothing more.

Needs `websocket-client` (already in the repo's venv). Set `KILN_HOST=host:port` to point elsewhere
(default `192.168.1.125:8081`); logs go to `~/backups/kiln-profiling` (`KILN_LOG_DIR` to change).

## profile_monitor.py

Watches a run, writes every status message to a CSV, and sends Stop (`POST /api`) if a rule trips.

```
python profile_monitor.py --selftest                                   # 24 cases on fake data; touches nothing
python profile_monitor.py --profile NAME --profile-file F             # dry run: shows the plan, starts nothing
python profile_monitor.py --profile NAME --profile-file F --go        # starts the run (kiln must be IDLE, 5 to 45 C) and supervises it
python profile_monitor.py --profile NAME --profile-file F --attach    # supervise a run already running, or started within 3 minutes
```

Useful options: `--extra-min` (time allowed beyond the profile, because the controller pauses the schedule while
the kiln catches up; use 240 for a long firing), `--ceiling C` (absolute ceiling, default 1050; set it just
above `emergency_shutoff_temp`), `--after-min` (how long to log after the run ends), `--feed-lost-min`.
Exit code 0 only when the profile finished normally.

Rules (constants at the top of the file): absolute ceiling; more than 30 C above the profile's peak;
heater on 10 C above target; 25 C above target for 2 minutes; 40 C below target for 10 minutes; a rise of
more than 30 C/min; no rise after 10 minutes of heating; the kiln cannot reach the target (20 minutes at
90% or more output gaining under 5 C while at least 4 C below target); heater reported off but the
temperature still climbing (a stuck relay, which prints CUT THE POWER AT THE WALL); status feed silent.

Note: the controller's `heat` field is seconds on per cycle (0 to `sensor_time_wait`, 2 s by default), not a
fraction. This tool divides by `HEAT_CYCLE_S`.

## swap_profile.py and start_profile.py

`start_profile.py NAME` starts a profile on an IDLE controller. `swap_profile.py NAME` replaces the running
profile: Stop, wait for IDLE, run NAME. The controller can only start from IDLE, so the heater is off for about
two seconds and the PID's integral is reset, which makes the kiln dip a few degrees. Use only with someone at
the kiln, and run a monitor in `--attach` mode first.

## lamp_test.py and ws_status.py

`lamp_test.py --go` runs a two minute profile against a lamp in place of the kiln to prove the
controller, the SSR and the pin work before the kiln is connected. `ws_status.py` prints a few status messages.

## bisque-850c.json

The bisque schedule for that kiln (14.3 hours including the controlled cool-down to 500 C), copy it to the Pi's `storage/profiles`.
It peaks at 850 C because the kiln could not hold 865 C even at full power. Ramps are what the kiln followed
with the PID's window held: 31 C/h to 90 C, 200 C/h to 538 C, 120 C/h to 760 C, 80 C/h to 850 C, a
30 minute hold, then a controlled cool-down (-200 then -100 C/h) to 500 C that slows through quartz
inversion (573 C). The profile ends at 500 C and the controller switches the heater off: below that the
kiln cools more slowly than any schedule worth asking for (the first cool-down never used the heater again
below 500 C and ran hours behind), and nothing in a bisque matters below quartz inversion. Let the kiln
cool with the lid shut. The controller can only slow cooling by feeding heat, never speed it up.

## raku.json

Heats bisqued, glazed raku pots to 850 C and stops, so they can be lifted out hot into sawdust (6.5 hours).
20 to 100 C at 100 C/h, 20 minute hold (drives off damp glaze), 200 C/h to 538 C, 120 C/h to 760 C,
80 C/h to 850 C, 15 minute hold, then the run ends and the heater goes off. There is no cool-down and no
hold at 316 or 538 C: the pots are already bisqued. Ramps are the ones the kiln has followed (see
bisque-850c); raku clay would tolerate faster, so they can be tightened after a first firing. 850 C is the
top of what this kiln can hold (it plateaus near 865 C at full power), so the glaze must mature at or below
about 850 C. Not yet fired. The kiln cools about 200 C/h at 860 C once the heater is off, so be ready at the
kiln before the 15 minute hold ends.
