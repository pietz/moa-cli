# 002 - Attribution policy (human always named; synthesizer always blind)

**Status:** done
**Touches:** `src/moa_cli/cli.py` (remove `--blind`; synthesis always anonymizes its input), `tests/test_moa.py`, `README.md`

## Decision (from the user)

- The human/agent reading moa's output **always gets correct attribution** - real
  provider names on every response block. There is **no human-facing
  anonymization toggle**.
- Anonymization exists for one reason: so the **synthesizer can't pick favorites**
  by brand. So the synthesizer **always** receives the proposer answers
  anonymized ("Response A/B/C") and order-shuffled. Always-on internal behavior,
  not a user flag. ("Pass on the anonymity" = apply it when building the
  synthesizer prompt; the human side stays named.)

## Implications

- **Remove the `--blind` CLI flag.** Synthesis is unconditionally blind + shuffled
  internally; there is nothing to toggle.
- The A->provider map stays **internal plumbing**; it does NOT need to be shown to
  the human (they already see real names on the response blocks). Drop the
  "Blind labels: A=claude..." stderr note.
- The synthesized answer is brand-agnostic prose (the MoA aggregator prompt
  already instructs not to reference "Response A" etc.).
- Same policy extends to the debate judge in 004: judge sees anonymized +
  shuffled transcript; human sees named turns.

## Acceptance criteria

- [x] `--blind` flag removed from `ask`.
- [x] Synthesis always builds its prompt from anonymized + order-shuffled answers.
- [x] Human-facing response blocks always show real provider names.
- [x] No A/B/C labels leak into human-facing stdout/stderr.
- [x] `--json`: per-model records keep real provider names; synthesis record needs
      no exposed `label_map` (keep internal). Confirm final JSON shape.
- [x] Tests: synthesis input is anonymized + shuffled; output blocks named; no `--blind`.
- [x] README: remove `--blind`; explain the always-named / always-blind-synth policy.
