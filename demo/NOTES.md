# moa README demos - working notes

Three terminal GIFs for the README "Three modes" section: one per command.

## How these are built (staged, not live)

The GIFs do **not** call the real agents. Earlier attempts recorded live runs,
but real latency made them slow (ask ~32s, debate ~70s) and non-deterministic.

Instead each demo is **staged**:

- `_engine.py` reuses moa's own `StatusLine` spinner and block renderers
  (`render_block`, `render_synthesis_block`, ...), so the output is byte-for-byte
  what `moa` actually prints - including the multi-line spinner and per-mode
  reveal behavior.
- `ask.py` / `distill.py` / `debate.py` hold canned content (inspired by real
  runs) and the timing for each turn, then call the matching `play_*` coroutine.
- The `.tape` files shadow `moa` with a shell function
  (`moa(){ uv run python demo/$1.py }`) so the on-screen command still reads
  `moa ask "..."` while the staged player runs underneath. `uv` is warmed
  off-screen so there's no startup stall.

## Re-rendering

```bash
vhs demo/ask.tape      # -> demo/ask.gif
vhs demo/distill.tape  # -> demo/distill.gif
vhs demo/debate.tape   # -> demo/debate.gif
```

To tweak content or pacing, edit the `Turn(..., elapsed=...)` entries in the
per-mode `.py` files (elapsed drives both the block header and how long the
spinner runs), then bump the `Sleep` in the matching `.tape` if total runtime
changes.
