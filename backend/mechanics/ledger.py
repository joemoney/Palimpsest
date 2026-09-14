"""`progression` / `spendable_ledger` - engine v2 phase 5, port 2 of 2.

docs/ENGINE_V2_SPEC.md §7.8, docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md phase 5.
Owns the ledger spec §7 calls a ratchet: durable gains the model names and the engine numbers,
marks spent, and evicts.

**A relocation, not a redesign**, for the same reason `beat_counter` is. §7.8: "the model already
emits labels and kinds rather than arithmetic, and the engine already owns numbering,
spend-matching and eviction." All this port does is move that ownership behind the registry.

**Spent entries are retained, not pruned.** They are cheap, they enable callbacks, and
`history.compressed_summary` is already lossy - so a spent-but-retained entry may end up the only
surviving record that something was ever gained. `LIMIT` is what keeps that from growing forever
(docs/ARCHITECTURE.md, "Keeping LLM Context Bounded"), and it deliberately gives up rather than
dropping a live asset: if the list is over `LIMIT` but every entry is unspent, the overflow
stands. An unspent entry is a promise the release directive can still cash, and silently deleting
one would make the directive point at something the story never delivered.

**Two fields, deliberately, for now** - `leverage_gained` and `leverage_spent`, which §5.4 wants
as one richer field. Not merged here for the reason `beat_counter` gives at length: phase 5's gate
is `test_pacing_loop.py` passing unmodified, and that file asserts on both names. The merge is its
own step with its own measurement.

**Still matched by exact label string**, the pattern `items_lost` used before phase 4 gave
inventory real ids - and the reason the unspent labels are shown verbatim in the prompt. Giving
ledger entries the same treatment items got is a real improvement and a real change, so it is not
smuggled into a relocation.
"""
import json

from . import Effect, MechanicEngine, ObservationField, register, register_effect

# Spec §7's retention/bounding policy for protagonist.leverage.
LIMIT = 40


class SpendableLedger(MechanicEngine):
    slot = "progression"
    name = "spendable_ledger"
    # After beat_counter (70), before failure (90). Nothing else reads the ledger during
    # resolve; this only has to be stable.
    resolve_order = 80
    # §5.4. Contributes no narration section of its own: the ledger reaches the narrator
    # through the pacing directive's {unspent_leverage} interpolation, which story_engine
    # assembles.
    prompt_budget = 0

    # --- configuration -------------------------------------------------------------

    def label(self, cfg):
        """What this story calls the ledger. Defaults to "leverage" because that is what the
        field names already say; a story that calls it "unlocks" only changes what the player
        and the model see, never the storage."""
        return cfg.get("label", "leverage")

    def kinds(self, cfg):
        return cfg.get("kinds", [])

    @staticmethod
    def entries(ctx):
        return ctx["state"]["protagonist"].get("leverage", [])

    def unspent(self, cfg, ctx):
        """Only unspent entries are ever shown (spec §11): a spent one is retained for the record,
        not for the directive to keep pointing at."""
        return [e["label"] for e in self.entries(ctx) if not e.get("spent")]

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        """Two fields - see the module docstring on why they are not yet one."""
        kinds = self.kinds(cfg)
        hint = cfg.get("prompt_hint", "")
        label = self.label(cfg)
        unspent = self.unspent(cfg, ctx)
        gained = ObservationField(
            "leverage_gained",
            f'  "leverage_gained": [{{"kind": "<one of: {", ".join(kinds)}>", "label": "<short, '
            f'concrete description of a durable gain the protagonist did not have before this '
            f'turn{" - " + hint if hint else ""}>"}}]',
            f"\nCURRENT {label.upper()} (do not repeat in leverage_gained; copy a label "
            f"verbatim from here for leverage_spent): {json.dumps(unspent)}",
        )
        spent = ObservationField(
            "leverage_spent",
            f'  "leverage_spent": ["<the exact label, copied verbatim from CURRENT '
            f'{label.upper()} above, of every entry this turn used up or invalidated - '
            "spent when it has been cashed in and can't be cashed again, or when events "
            'made it worthless. Not merely mentioned or acted on. [] if none>"]',
        )
        return [gained, spent]

    def events(self, cfg, ctx, diff):
        """A gain with no label, or with a kind outside the authored list, is dropped - the
        vocabulary is the story's and an entry outside it is a mechanic the author never wrote."""
        kinds = self.kinds(cfg)
        events = []
        for gain in diff.get("leverage_gained") or []:
            label = gain.get("label")
            kind = gain.get("kind")
            if not label or (kinds and kind not in kinds):
                continue
            events.append({"type": "leverage_gained", "kind": kind, "label": label})
        for label in diff.get("leverage_spent") or []:
            events.append({"type": "leverage_spent", "label": label})
        return events

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """Mint gains, then mark spends, then evict.

        Gains before expenditures, mirroring the inventory engine's order, so a gain cashed in
        within the same turn resolves correctly."""
        entries = self.entries(ctx)
        turn = ctx["state"]["pacing"].get("turn_count", 0)
        next_number = _next_number(entries)
        projected = [dict(e) for e in entries]
        effects = []

        for event in observations or []:
            if event.get("type") != "leverage_gained":
                continue
            entry = {
                "id": f"lev_{next_number:03d}",
                "kind": event["kind"],
                "label": event["label"],
                "acquired_turn": turn,
                "spent": False,
            }
            projected.append(entry)
            effects.append(Effect("progression.gain", reason=f"kind:{event['kind']}",
                                  entry=entry))
            next_number += 1

        for event in observations or []:
            if event.get("type") != "leverage_spent":
                continue
            for entry in projected:
                if entry["label"] == event["label"] and not entry.get("spent"):
                    entry["spent"] = True
                    entry["spent_turn"] = turn
                    effects.append(Effect("progression.spend", reason=f"turn:{turn}",
                                          label=event["label"], turn=turn))
                    break

        for index in _evictable(projected):
            effects.append(Effect("progression.evict", reason="over limit",
                                  id=projected[index]["id"]))
        return effects


def _next_number(entries):
    """Mirrors _next_subplot_id's numbering scheme for protagonist.leverage entries (spec §7's
    "lev_004" ids), which is a list rather than an id-keyed dict."""
    numbers = [
        int(entry["id"].rsplit("_", 1)[-1])
        for entry in entries
        if entry.get("id", "").rsplit("_", 1)[-1].isdigit()
    ]
    return (max(numbers) + 1) if numbers else 1


def _evictable(entries):
    """Indices of the spent entries to drop, oldest first. List order is acquisition order, since
    entries are only ever appended. Mirrors archive_stale_flags' role for flags.active, not its
    mechanism: flags age out by turn, a ledger entry ages out only once the story has used it up."""
    if len(entries) <= LIMIT:
        return []
    spent = [i for i, entry in enumerate(entries) if entry.get("spent")]
    return spent[: len(entries) - LIMIT]


def _apply_gain(ctx, effect):
    ctx["state"]["protagonist"].setdefault("leverage", []).append(dict(effect.payload["entry"]))


def _apply_spend(ctx, effect):
    for entry in ctx["state"]["protagonist"].get("leverage", []):
        if entry["label"] == effect.payload["label"] and not entry.get("spent"):
            entry["spent"] = True
            entry["spent_turn"] = effect.payload["turn"]
            break


def _apply_evict(ctx, effect):
    leverage = ctx["state"]["protagonist"].get("leverage", [])
    for index, entry in enumerate(leverage):
        if entry.get("id") == effect.payload["id"]:
            leverage.pop(index)
            break


ENGINE = register(SpendableLedger())
register_effect("progression.gain", _apply_gain)
register_effect("progression.spend", _apply_spend)
register_effect("progression.evict", _apply_evict)
