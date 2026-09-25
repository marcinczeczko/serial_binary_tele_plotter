# ADR-0011: Text commands are a terminal

- Status: proposed (2026-09-25)
- Supersedes: nothing; extends ADR-0010 (device profiles and text-line streams), whose
  point 5 left text commands for R8.5.

## Context

A text profile's board prints lines and reads lines. Binary boards are driven by command
panels built from `commands` and `panels` in config (ADR-0007). A text board is usually
driven by hand, the way Arduino's Serial Monitor is used: type a line, send it, read the
answer. A first draft proposed command templates in config (`"PID {kp} {ki}\n"`) to reuse
the panels; the owner chose a terminal instead.

## Decision

1. **A text profile sends from a terminal**: an input line in the Controls dock. Enter
   sends the typed text plus the profile's chosen line ending (LF by default, CR LF, CR
   or none, remembered per profile). Up/Down recall the session's lines.
2. **No config for sending.** A text profile's `commands` and `panels` are ignored with a
   warning, because they'd send binary packets to a text board. No schema change.
3. **The board's replies are shown**: lines that match no stream pattern, in the
   terminal's transcript. They reach the GUI in the ~1 Hz link report, at most 100 per
   report (the rest counted), so no per-line signal crosses threads (ADR-0002).
4. **Sends stay bytes.** The GUI encodes the line (ASCII only; anything else is refused)
   and hands the engine bytes, as for binary packets. Each send is numbered and marked on
   the plot, like a panel send.

## Consequences

- A text board needs no command definitions; any firmware's command syntax works.
- Live mode and presets (R6.4) don't apply to text profiles. If typing the same lines
  gets repetitive, templates can be added later on top of the terminal.
- Binary profiles are unchanged, byte for byte (the pinned `0x10`/`0x11` tests stand).
