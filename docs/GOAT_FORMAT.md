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

## Resolved rulings

1. **Fusion toolbox size.** `GOAT_FUSION_TOOLBOX_V1` is officially **22 cards**.
   The earlier "21" was a counting error; the itemised list is authoritative and
   nothing is cut. It remains a literal GOAT-era Fusion Deck, not a modern
   15-card Extra Deck, and a test pins the size at 22.
2. **Copy-limit corrections (v1.1).** The April 2005 list applies across
   Main + Side + Fusion combined, which put three decks over the limit. Main
   Decks were preserved wherever practical:
   * `PANDA_BURN_V1` — Side: -1 Ceasefire, -1 Magic Cylinder,
     +1 Dust Tornado, +1 Solemn Judgment.
   * `REASONING_GATE_TURBO_V1` — Side: -1 Jinzo, +1 Mobius the Frost Monarch.
   * `EARTH_BEAT_V1` — Main: Injection Fairy Lily 2->1, Enraged Battle Ox 2->3.
     Side: -1 Exiled Force, -1 Dust Tornado, +1 King Tiger Wanghu,
     +1 Kinetic Soldier.

   All ten decks now validate against the authoritative LFList. Deck `version`
   stays `1` (the ids are `*_V1`); `deck_hash` is the change-detection
   mechanism and changed for all three corrected decks.
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

> **This changes reproduction of historical YGO-Bench runs.** Any replay or
> metric produced before this fix was generated from an unshuffled deck and will
> not reproduce. Pass `shuffle_decks=False` to `run_duel` to reproduce that
> legacy behaviour deliberately; it is not the default and should not be used
> for new benchmark runs.

## Reproducibility

A run is reproducible given the engine build, CardScripts commit, card database,
format profile, deck hashes and seed. The deck manifest records the card
databases, lflist, and the `yugi-bench`/CardScripts commits; each duel replay
records the format profile and the resolved duel flags.

## GOAT ruling suite (Milestone 0.2)

`tests/goat_harness.py` drives a real `DUEL_MODE_GOAT` duel with a stacked deck
so a scenario can put known cards in hand and then play legal actions through
the same call path the duel runner uses. It chooses deck contents and order and
nothing else -- no engine interaction is bypassed.

Findings from batch 1 (legacy priority, Scapegoat, Metamorphosis, Chaos
Sorcerer), all observed from the engine:

* **Legacy ignition priority is implemented.** After Normal Summoning Breaker
  the Magical Warrior, GOAT offers its ignition effect to the summoning player
  in a chain window immediately after the summon. The identical scenario under
  Master Rule 5 offers nothing in that window. This is the differential the
  test asserts, so it cannot be explained by the scenario alone.
* **Scapegoat is the pre-errata card.** It makes four Level 1 Sheep Tokens and
  registers `CANNOT_SUMMON` / `CANNOT_FLIP_SUMMON` / `CANNOT_SPECIAL_SUMMON`
  for the turn. Setting a monster is still allowed (Setting is not Summoning,
  and the 2005 text does not forbid it). Because Special Summoning is locked,
  Metamorphosis on a Sheep Token is necessarily a two-turn play -- exactly how
  the line was played in 2005.
* **Metamorphosis matches Levels strictly.** From a Sheep Token (Level 1) the
  engine offers only Thousand-Eyes Restrict; from Chaos Sorcerer (Level 6) it
  offers exactly the Level 6 Fusions in the 22-card toolbox (Ryu Senshi, Dark
  Blade the Dragon Knight, Ojama King, Dark Flare Knight, Roaring Ocean Snake)
  and not Thousand-Eyes Restrict.
* **Chaos Sorcerer** Special Summons by banishing one LIGHT and one DARK from
  the graveyard, verified by inspecting the banished zone afterwards.

Note for scenario authors: `Thunder Dragon` is **LIGHT**, not DARK -- which is
why GOAT Chaos decks run it as LIGHT fodder.

### Action-space defect found while building the suite (fixed)

`legal_actions_from_pending` emits the upstream passive response first and then
de-duplicates by `(tool, arguments)`. For `select_unselect_card` prompts --
Chaos Sorcerer's banish cost, for example -- the passive response is literally
"pick index 0", so first-wins de-duplication deleted the concrete choice for the
first selectable card. The card became unreachable by label, and the entry
labelled "passive / first legal" silently performed a selection instead of
declining. Any card with a select/unselect cost was affected, LLM agents
included.

Fixed in two parts:

* When a concrete choice collides with the passive entry, the labels are merged
  (`passive / first legal / select Magician of Faith`) rather than the choice
  being dropped, so every payload stays in the action set and stays findable.
* Select/unselect choices are now labelled by card name and by whether they
  select or unselect, instead of `card index N`.

Covered by unit tests that drive the action space with a synthetic prompt, so
they run without a built engine.

### Batch 2 findings — board control

* **Thousand-Eyes Restrict** equips the opponent's monster on summon: the
  monster leaves their field entirely and TER takes on its ATK/DEF (1900/1600
  from a Luster Dragon). The equip is an optional trigger, so an agent that
  declines the chain window keeps a 0/0 TER and leaves the monster where it was.
* **Book of Moon** flips a face-up monster to face-down Defense, and the
  flipped card's identity stops being visible to the opponent -- hidden
  information is re-established, not merely hidden at draw time.
* **Creature Swap** exchanges control one monster each way, Sheep Tokens
  included.
* **Nobleman of Crossout** banishes a Set Flip monster *and every same-named
  copy in the Deck* (verified: three Magician of Faith banished, deck count down
  by two) and sends nothing to the graveyard.
* **Tsukuyomi**: the Flip Summon is offered as a reposition of the Set monster;
  its FLIP effect turns a face-up monster face-down, and the Spirit returns to
  the hand at the End Phase.
* **Sinister Serpent** is offered from the graveyard specifically in the
  Standby Phase, and the test pins the phase so an any-time recovery would fail.

Still queued for batch 3: Ring of Destruction, Jinzo/trap suppression, Call of
the Haunted lifecycle, Spirit Reaper, Damage Step activation restrictions and
chain construction/resolution order.

### Batch 3 findings — traps, lifecycles, Damage Step, chains

* **Ring of Destruction (Pre-Errata)** cannot be activated on the turn it is Set,
  and on resolution destroys the targeted face-up monster and deals its ATK to
  **both** players (1900 each, taking both to 6100).
* **Jinzo** removes Trap activation from the *legal action set*, not merely from
  the outcome: with no Jinzo the opponent is offered `activate Jar of Greed`;
  with a face-up Jinzo the option is absent entirely.
* **Call of the Haunted** revives the target, and destroying the Call takes the
  revived monster with it.
* **Spirit Reaper** is destroyed when targeted by Snatch Steal -- before the
  equip can take control of it -- and survives battle (1600 damage through, no
  graveyard entry). But see the nuance below.
* **Damage Step restrictions** are real and observable: Book of Moon is offered
  in the pre-damage chain windows and absent in every window inside the Damage
  Step. The step is bracketed by the engine's own `MSG_DAMAGE_STEP_START` /
  `MSG_DAMAGE_STEP_END` messages, so no hand-written table of Damage Step legal
  cards exists anywhere in the suite.
* **Chains** build and resolve correctly: a two-link chain (Book of Moon then
  Ring of Destruction) resolves last-in-first-out, both players get a window
  while it is open, and link 1 resolves with nothing to do because link 2
  already destroyed its target.

#### Engine nuance worth knowing: Book of Moon beats Spirit Reaper

Targeting Spirit Reaper with Book of Moon does **not** destroy it -- it is
simply flipped face-down and survives. Spirit Reaper's self-destroy is a
continuous effect ranged to `LOCATION_MZONE`, so once the same resolution has
turned it face-down the effect no longer applies when the chain solves. This is
recorded as observed engine behaviour rather than asserted from card text.

#### Constant spelling

The battle-step flag really is spelled `DUEL_6_STEP_BATLLE_STEP` -- the typo is
in Project Ignis's own `ocgapi_constants.h` (line 382), not in our notes. It is
part of the GOAT mask (0x8), as are `DUEL_SINGLE_CHAIN_IN_DAMAGE_SUBSTEP`,
`DUEL_TCG_SEGOC_NONPUBLIC` and `DUEL_TCG_SEGOC_FIRSTTRIGGER`.

## Notes for the future AI layer (not implemented yet)

These are recorded for the eventual Duel AI design; no policy is built here.

* **"Optional" does not mean "usually decline."** Thousand-Eyes Restrict's equip
  is an optional trigger. A generic decline-optional-chains policy summons a 0/0
  TER and leaves the opponent's monster untouched, throwing away the entire
  point of the card. The agent will need to distinguish optional
  value-generating triggers from genuinely skippable ones.
* **Flip Summon surfaces as `repos`.** The action layer exposes a Flip Summon as
  a reposition of a Set monster rather than as its own command. That is an
  action-semantics issue to resolve in the agent abstraction, since a model
  reasoning about "Flip Summon" will not find a command by that name.
