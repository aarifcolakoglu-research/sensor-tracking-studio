# Sensor Tracking Studio

Calibration and live monitoring for a 12-channel force sensor array.

The array is read by a Teensy 4.1 running `firmware/helmet_12_sensor_array`, which reports a **socket** (`A-1` … `C-4`), not a
sensor. There are 37 calibrated sensors for those 12 sockets, so the application
has to be told what is plugged in before it can turn a raw value into a
reading. Each sensor is calibrated against two quantities at once - the force
on it and how far the calibration indenter had pressed into the elastomer - and
the application reads both live. That mapping, the calibration that produces
the equations, and the live monitoring all live in one window.

It was used to calibrate the sensors and record the helmet–head-model force
measurements in *Evaluation of an Instrumented Helmet for Measurement of
Orthosis–Head Interface Forces in Pediatric Craniosynostosis* (Colakoglu *et al.*,
manuscript under review). See [Configuration used in the helmet study](#configuration-used-in-the-helmet-study).

---

## Install as a Windows application

Produces a Desktop and Start-menu shortcut that opens the app with one click.
No Python and no PyCharm needed on the machine that runs it.

```bat
pip install -r requirements.txt
pip install -r packaging\requirements-build.txt
packaging\build.bat
```

Build from the same environment the application runs in, so the bundle
carries the library versions the project is developed against.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\install.ps1
```

The build lands in `dist\SensorTrackingStudio\`; the install copies it to
`%USERPROFILE%\SensorTrackingStudio` and creates the shortcuts.

Re-running `install.ps1` upgrades in place and leaves your data alone
(`input_raw_data\`, `datasets\`, `config\`, `outputs\`, `reports\`,
`visualizations\`). Useful switches:

| Switch | Effect |
|---|---|
| `-SeedRawData` | also copy the checkout's raw CSVs (gigabytes; only a re-calibration needs them). The equations file is seeded either way |
| `-NoDesktop` | Start menu only |
| `-Dest <path>` | install somewhere else |
| `-RefreshIcons` | clear Windows' icon cache if a shortcut still shows the old picture |

It installs under the user profile rather than `Program Files` because the app
writes its data folders next to its own `.exe`, and `Program Files` is
read-only for normal users. It sits at the root of the profile rather than in
`AppData` because those folders are working data - recordings come out of
`outputs\` and the raw calibration CSVs are put into `input_raw_data\` by hand -
and `AppData` is hidden by default, which makes the install invisible to the
person who has to reach into it.

Upgrading does not touch `datasets\`, so an equations file with new columns has
to be copied across by hand; the application hides what it cannot find rather
than failing, so a missing column shows up as a missing feature.

### Running from a checkout instead

```bash
pip install -r requirements.txt
python main.py
```

Both ways behave identically. `app_paths.py` resolves the data folders to the
project directory when running from source and to the folder holding the `.exe`
when packaged, and creates them on first run.

---

## The three pages

### Setup — calibration and sensor selection

The left panel runs the calibration; the right one says what is in each socket.

**Calibration** reads every CSV in `input_raw_data/` and produces
`datasets/sensor_fit_equations.csv` in three stages, each of which leaves its
output on disk:

```
input_raw_data/*.csv
    |  stage 1  label each sample with its step, cycle and phase
    v
datasets/metadata_fz.csv              ~1 GB for 37 sensors
    |  stage 2  per-step min / max / median / mean
    v
datasets/metadata_fz_median.csv       ~1.5 MB
    |  stage 3  fit the polynomials
    v
datasets/sensor_fit_equations.csv     ~100 KB
```

It takes about two minutes for 37 sensors and runs on a worker thread, so the
window stays responsive; the panel above the button names the three stages and
reports the first one file by file, since that is where most of the time goes. Every stage also writes a timestamped copy into
`datasets/archive/`, keeping the newest two of each and deleting older ones —
stage 1 alone is about a gigabyte per run.

The intermediates are kept on purpose: the per-step distribution and the
between-cycle spread are read from them for the publication work.

**Model source** picks which equation the live readings come from. Every
calibration writes all of them, so both choices are free to change and need no
re-run:

| | |
|---|---|
| Fit equation | `Quadratic (degree 2)` or `Cubic (degree 3)` |
| Cycles | `Combined (all cycles)`, or any single cycle |

Degree 2 is the application default when no saved configuration exists, so
that readings recorded against earlier quadratic configurations do not change
silently. The configuration shipped in `config/` selects degree 3, which was
used in the helmet study. Degree 3 fits the same points about twice as closely — see
[Which equation to use](#which-equation-to-use) — but leaves the calibrated
range faster at the top.

**Sensor selection** maps the 12 sockets to sensors. Picking a set fills all
twelve at once (`S1_MatC`, `S1_MatG`, `S2_MatC`, `S2_MatG`); individual sockets
can then be changed for a mixed rig. Where a sensor has been calibrated more
than once, a second picker chooses which run to use — newest first, with each
option's R² shown. The choice is saved to `config/selected_sensors.json`; the
repository ships the mapping used in the helmet study.

### Dashboard — live cards

One card per bound socket, showing the socket, the sensor in it, the raw value,
the reading in two units, and a rolling chart. Connect to the Teensy, tare, and
record; `SIMULATION` drives the cards from the calibration curves when no
hardware is attached.

**Force** and **Indentation depth** tabs above each chart choose which of the two
the card shows: newtons and grams, or millimetres and microns. Each keeps its
own history and its own Y-range list, so switching tabs shows what was already
happening rather than an empty chart. Both are recorded whichever is on screen.

Every card has its own hysteresis curve and Y-range pickers, and the top bar
has an apply-to-all set, including one switch that moves all twelve cards
between force and indentation depth at once.

A socket that goes quiet — unplugged, a dead cable, or simply never bound —
flags itself: **NO DATA** appears next to its label and the big reading dims,
both after two seconds of silence rather than on the first missed sample, so a
socket that is merely slow to report is not flagged as faulty. The status bar
reports unreadable serial lines the same way it flags a socket, a few seconds
apart rather than one message per line, so a bad cable or a wrong baud rate is
visible without flooding the log.

The window starts within the available screen area. The dashboard keeps all
twelve cards in a 4-by-3 grid without scrolling. The compact toolbar shows only
the selected COM number; open its list to see full device descriptions. On very
narrow windows the toolbar can wrap while the cards stay on screen. Changing
the equation degree or cycle rebuilds the readings without resizing the window.

### Overlay — all sensors on one chart

One axes, one colour per socket, with the same Force and Indentation depth tabs
above the chart — newtons and gram-force, or millimetres and microns, left axis
and right. All the traces move together, because one axes cannot carry both.
The tiles along the top show and hide traces; the Y axis scales to what is
visible, so hiding the loud traces lets the quiet ones fill the chart. The sensor
strip wraps when there is not enough width for the full names, and can scroll
on short screens to leave space for the chart.

---

## The two measurements

The sensor is calibrated against two things at once. For every step of the
sweep the rig records the force on the reference load cell and the position of
the indenter, against the same raw magnetic reading. They are two different
functions of that one number — neither can be derived from the other without
knowing the elastomer — so each is fitted separately, and each gets its own
loading and unloading curve.

Displacement turned out to need the split as much as force does: 6.7% of full
scale in hysteresis against force's 8.8%. Pressing to a given raw value and
releasing to it are not the same state of the elastomer for either quantity.

### What to call it in a paper

In the interface it is **Indentation depth**. In a publication the reference
indenter still needs to be specified: this is the **indentation
depth equivalent to the calibration indenter** — how far *that* indenter, with
its own tip geometry, had travelled into the elastomer when the sensor read
this value.

It is not the depth of an arbitrary contact. A finger, a wider probe or a
softer one at the same force will not indent to the same depth, and the sensor
cannot tell them apart: it reports the calibration indenter's depth that
matches the field it is seeing. Any figure or axis label carrying this quantity
should say which indenter it refers to.

### The 2 mm ceiling

The elastomer is 3 mm thick with an embedded magnet of about 0.8 mm, so a
little over 2 mm of it can be compressed. Calibration reaches 1.80 mm.

Past 2 mm there is nothing left to indent, so a larger number is the polynomial
leaving the range it was fitted on, not a deeper press. Readings are therefore
held at 2.000 mm — on the charts and in the recordings alike, so the file and
the screen never disagree — and the fixed Y-range list stops at 2 mm. A trace
sitting flat at the ceiling is a fault to investigate, not a measurement.

There is no equivalent limit on force: the sensor is not bounded that way.

---

## The hysteresis curve

A sensor has two calibration curves, not one. Across the 37 sensors the
hysteresis error runs **7–10% of full scale**, far above the fit error, so
which curve is applied is a real choice:

| Mode | Reads from |
|---|---|
| Rising load | the curve calibrated with increasing load |
| Falling load | the curve calibrated with decreasing load |
| Auto select | whichever matches the direction the raw signal is moving |

`Auto select` uses a dead band of 0.5% of the sensor's calibrated raw span,
about 40 counts. Raw noise is 10–15 counts, so without one the direction would
flip on noise and the reading would jump between two curves that differ by most
of a newton at mid-range.

The mode is per socket and is saved with the binding.

---

## Equation file

`datasets/sensor_fit_equations.csv` is what the live engine reads.

| Column | Meaning |
|---|---|
| `sensor_id` | short identity, e.g. `S1_MatC_A1` |
| `set_id`, `position` | grouping label and socket position |
| `source_file` | which measurement this row came from |
| `collected_at` | when that measurement was taken |
| `cycle_id`, `fit_scope` | `single` rows are per cycle (1–5); `combined` rows (cycle 0) pool every cycle |
| `degree` | 2 or 3; every curve is fitted both ways |
| `target`, `unit` | `force` in `N`, or `displacement` in `mm`. The stored key stays `displacement`; the interface calls it Indentation depth |
| `phase` | `Loading` or `Unloading` |
| `coeff_x3`, `coeff_a`, `coeff_b`, `intercept_c` | `y = x3·bz³ + a·bz² + b·bz + c`, where `y` is whichever `target` the row is for, with `coeff_x3` zero on the degree-2 rows |
| `r2_score`, `n_points` | fit quality and how many points went in |
| `fz_max_n`, `pz_max_mm`, `raw_bz_min`, `raw_bz_max` | the calibrated range |
| `equation` | the same coefficients written out, for reading rather than for parsing |

For 37 sensors that is 1776 rows: 37 × 2 phases × 6 scopes × 2 degrees × 2
targets. A row is identified by all of those together; reading one without
filtering on `target` will mix newtons and millimetres.

The three original coefficient names are unchanged, so anything written against
the older file still reads the degree-2 rows correctly. A file without `degree`
or `coeff_x3` loads as degree 2, and one without `target` loads as force only —
the displacement tabs are then hidden rather than reading zero.

The **combined** rows pool the median points of all five cycles, about 92 points
per curve against 18 for a single cycle, which is why they are the default. The
per-cycle rows stay in the file for repeatability analysis and can be selected
on the setup page.

The measured range is carried in the file so the application is self-sufficient
about what each sensor can do: it caps the Y-axis ladder at what was actually
calibrated, and sizes the direction dead band.

---

## Which equation to use

Both degrees are fitted to the same points, so the difference is measurable
rather than a matter of preference. Across the 37 sensors, loading curves,
median over sensors:

| | Degree 2 | Degree 3 |
|---|---|---|
| RMSE over the whole range | 11.4 gf | **5.7 gf** |
| Worst error | 40.2 gf | **22.9 gf** |
| Worst error below 10% of full scale | 13.7 gf | **8.0 gf** |
| Offset at the sensor's own zero | 8.4 gf | 7.8 gf |

Degree 3 is closer on every measure and lower overall on 37 of 37 sensors. Its
cost is at the edges: 5% past the calibrated range it travels about twice as
far as the quadratic, which matters above the top of the range where any
reading is extrapolation anyway.

Two things worth knowing whichever is chosen:

**R² is a poor guide to low-force accuracy.** It is computed over a range
reaching about 1000 gf, so a 20 gf error near zero barely moves it. A curve can
report R² = 0.996 and still sit 20 gf above the data at the bottom of its
range.

**Every curve has an offset at its own zero**, 8.4 gf median for degree 2, and
each sensor's no-load raw value is different — 4811 to 7865 across the 37. The
same raw value therefore means different forces on different sensors, and
`TARE` is what removes both effects.

Regenerate the figures with:

```bash
python data_processing/low_end_accuracy.py     # writes reports/low_end_accuracy.csv
```

---

## Identity scheme

Raw filenames carry everything needed to identify a measurement:

```
2026-01-31-21-53-30_C_Z1p8_H10_C5_R20_S1_MatC_C4
|----- collected_at -----|-- conditions --|-set--|-pos-|
```

Only the set label and the position distinguish one sensor from another, so the
short identity is `S1_MatC_C4`. The test conditions are identical across all 37
files today and stay out of the identity, but are kept in case data is later
collected under other conditions. The full filename is kept as `source_file`,
which is what separates two measurements of the same sensor.

---

## Project layout

```text
Sensor_Tracking_Studio/
├── main.py                     application entry point and controller
├── app_paths.py                where the data folders are, source or packaged
│
├── calibration/
│   ├── pipeline.py             runs the three stages, with progress reporting
│   ├── equations.py            fits the polynomials, degree 2 and 3
│   ├── sensor_ids.py           filename -> sensor identity
│   └── archive.py              timestamped copies, newest two kept
│
├── data_processing/
│   ├── metadata_fz_v2.py       stage 1: step / cycle / phase labelling
│   ├── metadata_fz_median.py   stage 2: per-step statistics
│   ├── statistics.py           error and hysteresis report
│   ├── visualise_fitting.py    fit plots, per-cycle and combined
│   └── low_end_accuracy.py     zero offset and low-force error, per sensor
│
├── inference/
│   ├── polynomial_engine.py    raw -> force: degree, cycles, both curves, tare
│   ├── sensor_binding.py       socket -> sensor, and the equations catalogue
│   ├── teensy_serial.py        serial reading
│   ├── simulation_source.py    synthetic data when no hardware is attached
│   └── data_logger.py          session CSVs
│
├── gui/
│   ├── gui.py                  menu bar and the page stack
│   ├── theme.py                light / dark / mix, scale, series colours
│   ├── y_axis.py               Y-range choices
│   ├── icons.py                painted interface icons
│   ├── combo_box.py            combo boxes with readable popups
│   ├── sensor_card.py          one card
│   └── pages/                  setup, dashboard, overlay
│
├── firmware/                   Teensy 4.1 sketch for the 12-sensor array (see firmware/README.md)
├── packaging/                  Windows build and install
└── (data folders, git-ignored) input_raw_data, datasets, config,
                                outputs, reports, visualizations
```

---

## Running the pipeline without the GUI

```bash
python -m calibration.pipeline            # all three stages
python -m calibration.pipeline --from 2   # skip the slow labelling stage
python data_processing/visualise_fitting.py
python data_processing/statistics.py
```

---

## Session logs

`outputs/log_YYYYMMDD_HHMMSS.csv`, one row per socket per frame:
`timestamp`, `sensor_id`, `short_id` (the socket), `volume`, `raw_bz`,
`force_n`, `gram`, `displacement_mm`, `displacement_um`.

A frame is one pass of the source over every bound socket, written as a fixed
block: one row per socket, always in the same order, whether or not that
socket reported. Recording anchors itself on whichever socket is first to
actually report, rather than waiting on `A1` specifically, so a session with
`A1` unbound or unplugged still records the other eleven. A socket that stays
silent for a whole frame still gets its row, with `NaN` in every reading
column — read back by pandas and Excel as missing, and not confusable with a
sensor genuinely resting at zero.

Both measurements are on every row whichever tab was open, and they come from
one evaluation of the raw value, so the hysteresis phase behind them is the
same.

---

## Appearance

The top-right of the menu bar holds the theme (light, dark, or **mix** — dark
window furniture around light cards, the default), the interface scale from 70%
to 150%, and full screen. Scale changes apply immediately to fonts, controls,
spacing, popup rows and charts, without restarting or interrupting streaming
and recording. Dashboard cards adapt their density to keep the entire 4×3 rig
visible at smaller window sizes. Theme and scale are remembered in
`config/ui_settings.json`; operating-system display scaling remains independent.

The Dashboard toolbar wraps as three complete groups: connection and volume,
all-sensor settings, and session actions. Controls share a height and each group
has uniform internal gaps; free space is shared between groups. Setup stacks
Calibration (including RUN CALIBRATION), Current equations and Model source
in that order. Its summary table is left aligned, with right-aligned labels
and left-aligned values. SAVE SELECTION remains outside the scrolling rows.

Overlay traces are coloured by socket row — A warm, B green, C cool — so which
row a trace belongs to is readable from the chart without the legend.

## Interface regression checks

Run `python -m unittest discover -s tests -v` with the project dependencies
installed. The checks run Qt offscreen, keep saved bindings unchanged, and
exercise small-window layouts, model changes, full labels, chart bounds,
maximized windows and live zoom during simulation.
The model-change checks use the local calibration dataset and saved bindings.

---

## Configuration used in the helmet study

`config/selected_sensors.json` as shipped reproduces the acquisition settings of
the helmet experiments:

| Setting | Value |
|---|---|
| Sensor set | `S1_MatC`, sensors A1–C4 in sockets A1–C4 |
| Fit equation | cubic (degree 3) |
| Cycles | single cycle, cycle 3 |
| Hysteresis curve | rising load (loading branch) |
| Tare | `TARE ALL` in the unloaded state, before the helmet is fitted |

The force values in the published session logs can be recomputed exactly from
`raw_bz` with these rows of `datasets/sensor_fit_equations.csv`
(`set_id = S1_MatC`, `target = force`, `degree = 3`, `fit_scope = single`,
`cycle_id = 3`, `phase = Loading`). The logs and the analysis that produced
the figures are in
[helmet-contact-force-analysis](https://github.com/aarifcolakoglu-research/helmet-contact-force-analysis).

The raw calibration recordings (about 2.2 GB) are not stored here;
`datasets/metadata_fz_median.csv` holds the per-step medians from which every
equation in the file can be refitted (`python -m calibration.pipeline --from 3`).

## Related repositories

- [tactile-sensor-calibration-rig](https://github.com/aarifcolakoglu-research/tactile-sensor-calibration-rig): drives the three-axis rig that produced the calibration recordings
- [helmet-contact-force-analysis](https://github.com/aarifcolakoglu-research/helmet-contact-force-analysis): processing of the helmet force logs (Figure 5, Figures S9–S12 and Table S4)

## Citation

Please cite the article above and this software (see `CITATION.cff`).

## Licence

Code: [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0.html) (GPL-3.0-only).
Data in `datasets/` and `reports/`: [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). See `LICENSE`.
