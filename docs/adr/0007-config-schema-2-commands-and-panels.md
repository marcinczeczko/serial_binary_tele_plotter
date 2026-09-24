# ADR-0007: Config schema 2: versioned document, commands and panels in config

- Status: Accepted (2026-09-24, R5.1, R5.2, R5.3)
- Date: 2026-09-24
- Addresses: review findings A5 and C8
  ([review](../reviews/2026-09-24-architecture-review.md)).

## Context

- **A5.** `streams.json` had no version, so its format couldn't change safely. Only
  validation knew its shape, and saving was done by the Configuration tab itself.
- **C8.** The commands sent to the MCU were hard-coded for one robot:
  - IDs `0x10`/`0x11` and their `struct` formats in `core/protocol/constants.py`.
  - A PID panel with fixed parameters.
  - 10- and 20-argument signals through the container and the engine.
  - An IMU panel whose buttons could never do anything.

  Adding a command meant changing four layers of code.
- Nothing about the dashboard was remembered between runs: the port, the stream, hidden
  signals, lane moves and the panel's gains.

## Decision

1. **The document is versioned.**
   - Format:
     - `schema_version` (now `2`)
     - `commands`
     - `panels`
     - `streams`
   - A file without `schema_version` is version 1.
   - `core/config/migrate.py` upgrades a parsed document one version at a time, in memory.
   - The file is rewritten only when the user saves from the Configuration tab. The previous
     file is kept as `.bak`, and the new one is written to a temporary file and renamed.
   - A newer version than the app knows is refused rather than half-read.
   - `core/config` is a package:
     - `document`: load → migrate → validate → save.
     - `streams`: stream validation.
     - `controls`: commands and panels.
2. **Version 1 → 2.** A stream's `panel_type: "pid"` becomes `controls: "diffbot_pid"`.
   The `pid_single` (`0x10`) and `pid_both` (`0x11`) commands and the `diffbot_pid` panel
   are added unless the file already defines them. `panel_type: "imu"` is dropped, since
   that panel never sent anything. The migration is where the old hard-coded layouts now
   live, as data.
3. **Commands are data.** A command is `{label, packet_id, endianness, fields[]}`, framed
   like telemetry.
   - The payload uses the frame dtype code (`frame_dtype`), so a command and a frame with the
     same fields pack identically.
   - A field's value comes from, in order:
     - the button's `values`
     - the field's constant `value`
     - the panel parameter named by `param`, from the field's `column` or else the button's
   - The encoder refuses a value that doesn't fit its field. It never wraps or truncates a
     value that goes to hardware.
4. **Panels are data.**
   - A panel is a grid of parameters (`float` / `int` / `bool`, each with a default, range,
     step and decimals) by optional columns, plus buttons that send commands.
   - A stream names its panel with `controls`.
   - The DiffBot PID panel is one config entry. The generic `CommandPanel` replaces
     `PidTuningPanel` and `ImuCalibrationPanel`.
   - The GUI resolves and encodes a press, and hands the engine a finished packet over one
     `send_packet(bytes)` signal. There are no positional-argument signals any more.
5. **Broken commands or panels never block telemetry.**
   - Errors leave that command, and any panel that uses it, out, and are reported with the
     item's name.
   - A stream whose `controls` panel is missing or broken gets a warning and no panel.
6. **The simulator decodes commands with the configured layouts**, as firmware would. The PID
   motor model applies any decoded field named like one of its gains, for the motor in
   `motor_id` or with `left_`/`right_` prefixes. It never matches on packet IDs.
7. **Streams stay plain JSON objects** (typed as `StreamConfig`), not dataclasses:
   - The Configuration tab's editor round-trips them losslessly, including unknown keys and
     key order (C4). A dataclass model would have to reimplement that.
   - The document model (load, migrate, validate, save) and the parsed `CommandDef`/`PanelDef`
     dataclasses are what the rest of the app uses. The roadmap's "editor binds to the model"
     is scoped to this: the editor saves through `save_document`.
8. **The dashboard remembers its state in `QSettings`** (`ui/ui_state.py`):
   - Scoped globally: the port and baud rate.
   - Scoped per config file (by its path), because stream and panel keys only mean
     something within one file:
     - the shown stream
     - each stream's visibility and lane moves
     - each panel's values
   - View state is stored as overrides applied on top of `streams.json`, never written into
     it. View → "Reset view to streams.json" forgets them.

## Consequences

- A new command or panel, e.g. an IMU calibration, is a `streams.json` edit, with no code.
- The bundled `pid_single`/`pid_both` definitions produce byte-identical packets to the
  former hard-coded formats. A test pins this against `struct.pack("<BffffffffBB", …)` and
  `"<ffffffffBBffffffffBB"`. **Firmware-visible behaviour is unchanged.**
- Old files keep working. `tests/fixtures/streams_v1.json` (the last version 1 file) must
  migrate to exactly the bundled `streams.json`.
- Any future format change needs a migration step and a version bump, and this ADR
  superseded or extended.
- Remembered state can differ from `streams.json`. That's intended: the file is the shared
  definition, and the overrides are one user's view.
