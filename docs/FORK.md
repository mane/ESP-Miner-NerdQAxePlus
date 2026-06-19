# Community fork notes

This fork is maintained at:

https://github.com/mane/ESP-Miner-NerdQAxePlus

It started from `shufps/ESP-Miner-NerdQAxePlus` and may diverge when fixes or releases cannot be submitted upstream.

## Remotes

Recommended local setup:

```bash
git remote -v
# origin   git@github.com:mane/ESP-Miner-NerdQAxePlus.git
# upstream https://github.com/shufps/ESP-Miner-NerdQAxePlus.git
```

Use `origin/develop` as the active development branch for this fork. Keep `upstream` read-only unless an upstream sync is intentional.

## Change policy

- Keep fixes small and reviewable.
- Prefer objective firmware bugs with a visible failure mode or static regression coverage.
- Run the GitHub Actions board matrix before treating a firmware change as ready.
- Preserve original upstream credits and avoid implying upstream endorsement.
- For public build and update-channel steps, see `docs/RELEASES.md`.

## Current first fork-only fix

`b3f087925af2395d229dac21bf82d2bc48ab25aa` fixes Rev7 TPS546 VOUT limit programming so PMBus VOUT limit registers receive absolute voltages derived from `VOUT_COMMAND`, not raw ratio constants.

The corresponding fork PR passed the full board build matrix:

https://github.com/mane/ESP-Miner-NerdQAxePlus/pull/1
