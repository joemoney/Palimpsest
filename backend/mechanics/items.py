"""`inventory` / `tagged_items` - engine v2 phase 4, port 2.

docs/ENGINE_V2_SPEC.md §7.3, docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md phase 4 step 2. Owns items as
records rather than free strings, acquisition, consumption and capacity.

**This is where §5.1's field-count reduction actually comes from, and not for the reason
the spec predicted.** §7.3 expected the saving to be `items_gained` + `items_lost` merging
into one field. It is - but the larger one is that those two fields were *unconditional*.
Every story was asked about items every turn, including a courtroom drama and a regency
comedy of manners that have no inventory concept at all. That is a P-2 violation that
predates the registry and was invisible while inventory had no module to be absent from.
Declare-to-bind makes it structural: two fields for stories that want inventory, zero for
stories that do not.

**`items_lost`'s exact-string match is gone.** v2 removed an item by comparing the model's
string to the stored one character for character, and showed it `CURRENT INVENTORY` purely
so it could copy one back verbatim. That is the same fragility CLAUDE.md records for the
old `npc_id`-less relationship matching, with the same fix: mint an id at acquisition and
have the model cite the id.

**Legacy string entries still work.** A save written before this engine holds
`["a brass key"]`, not records. `_record()` reads a bare string as a label-only item, and
`used` falls back to a label match when the model cites something that is not an id - which
it will, for exactly those entries, because they have no id to cite. That fallback is for
pre-engine saves, not a second supported way to address items; new items always get an id.

**`used` means expended, not touched.** The engine decides what expending costs: an item
carrying `uses` loses one and survives until they run out, and anything else is removed.
Which means the default - for every item the narration invents on the fly, with no authored
record behind it - is exactly v2's behaviour. `uses` is the refinement a story opts into,
not a new rule imposed on stories that say nothing.

**Capacity refuses rather than evicts.** Over-capacity drops nothing: the gain is refused
and the narrator is told the protagonist is carrying all they can, so the refusal happens on
the page instead of an item silently vanishing from a save. Relationships evict because a
roster genuinely must be bounded for prompt reasons (CLAUDE.md); an inventory does not -
capacity here is a story rule, not a context-bounding measure, and it defaults to absent.
"""
from . import Effect, MechanicEngine, ObservationField, register, register_effect


class TaggedItems(MechanicEngine):
    slot = "inventory"
    name = "tagged_items"
    # After stats (20) and relationships (30): a gate or check reading "do they still have
    # the key" wants this turn's inventory, and nothing here reads a score or a stat.
    resolve_order = 40
    # §5.4. The PLAYER line is the whole roster of carried items; capacity is the only real
    # bound, and most stories author none. 900 is generous against the largest plausible
    # carried list and is meant to trip on a save that has accumulated junk for 200 turns -
    # which is a signal worth getting, not a limit to raise silently.
    prompt_budget = 900

    # --- configuration -------------------------------------------------------------

    def capacity(self, cfg):
        """None means unbounded, which is what every story that says nothing gets - and
        what CLAUDE.md already argued for: a story-appropriate item list does not grow the
        way flags and subplots do, so this is a story rule rather than a context bound."""
        return cfg.get("capacity")

    def tag_vocabulary(self, cfg):
        """Authored tags, if any. Offered to the model as a closed list when a story
        declares one; left open otherwise, since a tag nobody queries costs nothing and
        forcing every story to enumerate its nouns up front is authoring burden for no
        gain."""
        return list(cfg.get("tags") or [])

    # --- state ---------------------------------------------------------------------

    @staticmethod
    def items(ctx):
        return ctx["state"]["protagonist"].setdefault("inventory", [])

    @staticmethod
    def _record(entry):
        """One stored entry as a record, whatever shape it is on disk. A bare string is a
        pre-engine save's item and reads as label-only - see the module docstring."""
        if isinstance(entry, str):
            return {"id": None, "label": entry, "tags": []}
        return entry

    @classmethod
    def records(cls, ctx):
        return [cls._record(entry) for entry in cls.items(ctx)]

    @staticmethod
    def _next_id(records):
        """Mirrors insert_subplot/insert_character's numbering: scan for the highest
        existing number rather than counting entries, so removing an item never causes a
        later one to reuse a retired id."""
        highest = 0
        for record in records:
            item_id = record.get("id") or ""
            if item_id.startswith("itm_") and item_id[4:].isdigit():
                highest = max(highest, int(item_id[4:]))
        return f"itm_{highest + 1:03d}"

    def _find(self, records, cited):
        """The record the model meant, by id, then by exact label.

        The label fallback exists for pre-engine entries, which have no id to cite. It is
        deliberately exact rather than fuzzy: a near-match that silently consumed the wrong
        item would be worse than a miss, and a miss is a no-op the player can retry - which
        is exactly how v2's unmatched items_lost already behaved."""
        for record in records:
            if record.get("id") and record["id"] == cited:
                return record
        for record in records:
            if record.get("label") == cited:
                return record
        return None

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        """One field with a richer type, per §5.4 - acquisition and expenditure are two
        halves of the same question and an engine needing two fields is two engines.

        No cadence (§7.2/§7.3): picking something up is only legible about the turn it
        happened on."""
        records = self.records(ctx)
        tags = self.tag_vocabulary(cfg)
        tag_hint = (f'"<zero or more of: {", ".join(tags)}>"' if tags
                    else '"<short lowercase noun tags, e.g. key, document, tool>"')
        schema = (
            '  "inventory": {"gained": [{"label": "<short item description>", "tags": ['
            + tag_hint
            + ']}], "used": ["<the id of every item the protagonist expended, handed over, '
            'destroyed, or otherwise no longer has at the end of this turn>"]}'
        )
        context = f"\nCURRENT INVENTORY (cite these ids in used): {self._listing(records)}"
        instruction = (
            "For inventory.used, cite only items the protagonist no longer has or spent a "
            "use of - never one they used and still carry (a key turned in a lock, a lamp "
            "lit). Cite ids exactly as CURRENT INVENTORY shows them; never invent one, and "
            "never describe an item instead of citing it.\n"
        )
        full = self._capacity_notice(cfg, records)
        if full:
            instruction += full + "\n"
        return [ObservationField("inventory", schema, context, instruction)]

    def _listing(self, records):
        if not records:
            return "empty"
        return ", ".join(
            (f"{r['id']}: " if r.get("id") else "")
            + r.get("label", "")
            + (f" [{', '.join(r['tags'])}]" if r.get("tags") else "")
            + (f" ({r['uses']} uses left)" if isinstance(r.get("uses"), int) else "")
            for r in records
        )

    def _capacity_notice(self, cfg, records):
        capacity = self.capacity(cfg)
        if capacity is None or len(records) < capacity:
            return ""
        return (f"The protagonist is carrying all they can ({len(records)} of {capacity}); "
                f"they cannot take anything else up without putting something down.")

    def events(self, cfg, ctx, diff):
        """Read `inventory` back into typed events. Two event types rather than one, because
        the log is the raw observation stream (§8.2) and "gained a thing" and "spent a thing"
        are different observations even though they arrived in one field."""
        block = diff.get("inventory")
        if not isinstance(block, dict):
            return []
        events = []
        for gain in block.get("gained") or []:
            if isinstance(gain, str):
                # Tolerated, not encouraged: the model occasionally flattens the object
                # form, and a dropped acquisition is a player-visible loss.
                gain = {"label": gain}
            label = (gain or {}).get("label") if isinstance(gain, dict) else None
            if not label:
                continue
            tags = [t for t in (gain.get("tags") or []) if isinstance(t, str)]
            events.append({"type": "item_gained", "label": label, "tags": tags})
        for cited in block.get("used") or []:
            if isinstance(cited, str) and cited:
                events.append({"type": "item_used", "item": cited})
        return events

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """Three passes, not v2's two, and the reason is capacity.

        §6.2 records the sequencing that matters: gains before losses, so a thing picked up
        and spent in the same turn resolves. That was free while inventory was unbounded.
        With a capacity it is not: run gains first and "put the rope down, take the axe" is
        refused, because the axe is priced against a pack that is still full. Run losses
        first and a same-turn pickup-and-spend stops resolving, because the expenditure
        looks for something that has not been added yet.

        So: expenditures of things already held, then gains against the room that freed,
        then whatever expenditures are left - which can only be of something gained this
        turn. Both properties hold, and neither is an accident of statement order."""
        records = list(self.records(ctx))
        capacity = self.capacity(cfg)
        effects = []

        deferred = []
        for event in observations or []:
            if event.get("type") != "item_used":
                continue
            record = self._find(records, event["item"])
            if record is None:
                # Either nonsense, or a citation of something this turn will add. Pass 3
                # decides which; an unmatched expenditure stays a no-op either way, exactly
                # as v2's unmatched items_lost was.
                deferred.append(event)
                continue
            effects.extend(self._expend(records, record))

        for event in observations or []:
            if event.get("type") != "item_gained":
                continue
            if capacity is not None and len(records) >= capacity:
                # Refused, not dropped - and logged, so "where did the rope go" has an
                # answer. The narrator was already told they are full (see observations).
                effects.append(Effect("inventory.refused", reason="at_capacity",
                                      label=event["label"], capacity=capacity))
                continue
            record = {"id": self._next_id(records), "label": event["label"],
                      "tags": list(event.get("tags") or [])}
            records.append(record)
            effects.append(Effect("inventory.add", reason="item_gained", item=record))

        for event in deferred:
            record = self._find(records, event["item"])
            if record is not None:
                effects.extend(self._expend(records, record))
        return effects

    def _expend(self, records, record):
        """One expenditure. An item with uses left loses one and stays; anything else goes.

        Spending a use deliberately does not free capacity - the headlamp is still in the
        pack with one cell less in it."""
        uses = record.get("uses")
        if isinstance(uses, int) and uses > 1:
            return [Effect("inventory.spend_use", reason="item_used",
                           item=record.get("id"), label=record.get("label"),
                           remaining=uses - 1)]
        records.remove(record)
        return [Effect("inventory.remove", reason="item_used",
                       item=record.get("id"), label=record.get("label"))]

    # --- prompt ---------------------------------------------------------------------

    def prompt_sections(self, cfg, ctx):
        """The PLAYER-line fragment, and a footer only when the pack is full.

        An empty inventory still renders "nothing" rather than vanishing: unlike an empty
        relationship roster, "carrying nothing" is a fact about the scene the narrator needs,
        not an absent module. P-2 is about absent *modules*, and a bound engine with empty
        state is present."""
        records = self.records(ctx)
        labels = ", ".join(r.get("label", "") for r in records) or "nothing"
        sections = {"player_line": f" | Inventory: {labels}"}
        notice = self._capacity_notice(cfg, records)
        if notice:
            sections["footer"] = f"\n{notice} Do not have anyone hand them a new object until they put something down."
        return sections


def _apply_add(ctx, effect):
    TaggedItems.items(ctx).append(effect.payload["item"])


def _apply_remove(ctx, effect):
    """Remove by identity of the stored entry, re-found here rather than carried through the
    Effect: `resolve` is pure and worked on a copy, so the object it matched is the right
    *value* but the list this mutates is the live one."""
    items = TaggedItems.items(ctx)
    target_id, label = effect.payload.get("item"), effect.payload.get("label")
    for index, entry in enumerate(items):
        record = TaggedItems._record(entry)
        if (target_id and record.get("id") == target_id) or \
                (not target_id and record.get("label") == label):
            del items[index]
            return


def _apply_spend_use(ctx, effect):
    items = TaggedItems.items(ctx)
    for index, entry in enumerate(items):
        record = TaggedItems._record(entry)
        if record.get("id") == effect.payload["item"]:
            record["uses"] = effect.payload["remaining"]
            items[index] = record
            return


def _apply_refused(ctx, effect):
    """Nothing to write. The refusal is already in the event log via the observation that
    produced it and in the effect's own `reason`; this handler exists because
    `apply_effects` raises on an unregistered kind rather than dropping it, and an effect
    whose whole content is "this deliberately did not happen" still has to be declared."""
    return


ENGINE = register(TaggedItems())
register_effect("inventory.add", _apply_add)
register_effect("inventory.remove", _apply_remove)
register_effect("inventory.spend_use", _apply_spend_use)
register_effect("inventory.refused", _apply_refused)
