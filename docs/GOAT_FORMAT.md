# GOAT Format (April 2005) — Engine Foundation

GOAT support is layered onto YGO-Bench as additive modules so upstream changes
stay mergeable. Master Rule 5 remains the default; GOAT is opt-in.

## Architecture

```
Project Ignis / ocgcore      authoritative rules engine (DUEL_MODE_GOAT)
        │  legal actions + resolutions
        ▼
     duel agent                selects among legal actions
        │
        ▼
Project Ignis / ocgcore
```

Rules truth is the engine. No GOAT ruling is reimplemented in Python, in prompts
or in the agent layer. Deck *legality* is separate from gameplay *rulings*: the
engine performs no deck validation at all, so legality is enforced before the
duel is created.

| Module | Responsibility |
| --- | --- |
| `ygobench/formats.py` | `FormatProfile` registry (`mr5`, `goat_2005`) |
| `ygobench/cards.py` | name → card id against the pinned BabelCDB snapshot |
| `ygobench/legality.py` | `.lflist.conf` parsing and deck validation |
| `ygobench/goat/decklists.py` | the locked v1 reference lists (by name) |
| `ygobench/goat/build.py` | `.ydk` + versioned manifest generation |

Only one line of pre-existing engine code selects the ruleset, so GOAT reaches
ocgcore through `run_duel(..., duel_format="goat_2005")`.

## Usage

```bash
ygo-bench setup                       # build ocgcore + fetch card databases
python scripts/build_goat_decks.py    # regenerate resources/decks/goat/
ygo-bench duel --format goat_2005 --deck-root resources/decks/goat \
    --deck1 GOAT_CONTROL_V1 --deck2 CHAOS_CONTROL_V1 --agent1 random --agent2 random
```

## Card identity

The decklists store **names**; ids are derived mechanically. Two properties of
the Project Ignis data drive the resolver:

* The GOAT pool replaces many modern printings with format-specific entries —
  `… (GOAT)` from `goat-entries.cdb` and `… (Pre-Errata)` from
  `cards-unofficial.cdb`. Both qualifiers are stripped before matching, so the
  April 2005 card pool spans **three** databases: `cards.cdb` (1492),
  `goat-entries.cdb` (191) and `cards-unofficial.cdb` (21 pre-errata).
* `alias` does **not** always mean "same card". It marks alternate printings
  (`Jinzo` 77585514 → 77585513) but also "treated as" relationships between
  genuinely different cards (`Harpie Lady 1/2/3` → `Harpie Lady`,
  `A Legendary Ocean` → `Umi`). Candidates are collapsed only when they share a
  name *and* an alias group.

A name that matches nothing, or two genuinely different cards, is a hard error.
Similar cards are never substituted.

`GOAT.lflist.conf` is a `$whitelist`: 1705 entries, and anything absent is
illegal rather than unlimited.

## Open discrepancies

These are reported rather than silently corrected. The locked lists are
preserved verbatim.

1. **Fusion toolbox size.** `GOAT_FUSION_TOOLBOX_V1` is declared as 21 cards but
   the itemised list sums to **22**. The itemised list is preserved; truncating
   would mean choosing a card to drop.
2. **Copy-limit violations.** The April 2005 list applies across Main + Side +
   Fusion combined, which puts three decks over the limit:
   * `PANDA_BURN_V1` — Ceasefire ×2 (limit 1), Magic Cylinder ×2 (limit 1)
   * `REASONING_GATE_TURBO_V1` — Jinzo ×2 (limit 1)
   * `EARTH_BEAT_V1` — Dust Tornado ×4 (limit 3), Exiled Force ×2 (limit 1),
     Injection Fairy Lily ×2 (limit 1)
3. **`Kinetic Soldier` is a TCG name.** Project Ignis stores passcode 79853073 as
   `Cipher Soldier`. Resolved through an explicit, audited entry in
   `NAME_ALIASES`; every application is reported by the deck builder. This is a
   name mapping for the same physical card, not a substitution.

## Deck shuffling

ocgcore does **not** shuffle at duel start — it expects the host application to
supply an already-randomized deck, which is what EDOPro does client-side.
YGO-Bench previously added cards in `.ydk` order, so every duel with the same
decklists dealt identical opening hands regardless of seed. `_shuffled_main`
now shuffles per player from the duel seed, keeping runs reproducible while
making the seed meaningful.

## Reproducibility

A run is reproducible given the engine build, CardScripts commit, card database,
format profile, deck hashes and seed. The deck manifest records the card
databases, lflist, and the `yugi-bench`/CardScripts commits; each duel replay
records the format profile and the resolved duel flags.
