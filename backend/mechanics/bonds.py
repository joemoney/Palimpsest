"""`bonds` / `scored_bonds` - CR-11, NPC-to-NPC bonds (Story_Mechanics_Update.md CR-11).

One-way scores between two characters, kept at `state.mechanics.bonds[from][to] =
{score, peak, opened_turn}` - the place `conditions._bond` already reads. A pair opens lazily, on
its first event or at its `seed` value, and an unopened pair reads as 0, never unknown.

Like `scored_axis` the model never chooses a number: it names a register from the story's own
closed vocabulary (`bond_events`, one observation field) and the price list does the arithmetic.
`mutual: true` applies one event in both directions, which keeps this a single field with a
richer type instead of two.

Eviction: bonds between two authored characters are never evicted. A bond involving a generated
character counts against `max_generated_bonds` and the one closest to neutral goes first; a
character pinned by an active side thread is exempt, and when a character is evicted their bonds
go with them (`drop_character`, called from the relationships engine).

Tier labels only reach the narrator - never numbers - and only for pairs where both characters
are in the current scene, at most MAX_LINES lines.

Tier C: the observation pass already runs there, and this engine adds no call of its own.
"""
from . import Effect, MechanicEngine, ObservationField, register, register_effect

DEFAULT_SCALE = (-100, 100)
DEFAULT_MAX_GENERATED = 12
MAX_LINES = 4
WINDOW_KEY = "bonds_window"


def tier_for(tiers, score):
    """The band a score sits in, or None. A non-negative `at` reads as "at or above", a negative
    one as "at or below" - the same convention as the relationship tiers."""
    best = None
    for tier in sorted(tiers or [], key=lambda t: t["at"]):
        at = tier["at"]
        if at >= 0 and score >= at and (best is None or at >= best["at"]):
            best = tier
        elif at < 0 and score <= at and (best is None or at <= best["at"]):
            best = tier
    return best


def ledger(ctx):
    return (ctx["state"].get("mechanics") or {}).get("bonds") or {}


def pinned(ctx):
    """Characters cast in an active side thread. Read straight from that engine's state, because
    pinning is a promise between the two tracks and not an observation either owns."""
    block = (ctx["state"].get("mechanics") or {}).get("side_threads") or {}
    out = set()
    for thread in block.get("active") or []:
        out.update(v for v in (thread.get("cast") or {}).values() if isinstance(v, str))
    return out


def drop_character(ctx, name):
    """Remove every bond that starts or ends at `name` (a generated character was evicted)."""
    book = (ctx["state"].get("mechanics") or {}).get("bonds")
    if not book:
        return
    book.pop(name, None)
    for source in list(book):
        book[source].pop(name, None)
        if not book[source]:
            del book[source]
    window = (ctx["state"].get("mechanics") or {}).get(WINDOW_KEY)
    if window:
        for key in [k for k in window if name in k.split("|")]:
            del window[key]


class ScoredBonds(MechanicEngine):
    slot = "bonds"
    name = "scored_bonds"
    # Just after relationships (30): a character evicted from the roster this turn takes
    # their bonds with them, so bond eviction must see the roster as it will stand.
    resolve_order = 31
    # A header plus MAX_LINES pair lines of "A → B: tier · B → A: tier", names included.
    prompt_budget = 900

    # --- configuration -------------------------------------------------------------

    def check_config(self, cfg):
        if not cfg.get("registers"):
            raise ValueError(
                "mechanics.bonds declares engine 'scored_bonds' but authors no 'registers' "
                "price list. The model does not pick bond numbers; the story prices its own "
                "vocabulary (CR-11).")
        for tier in cfg.get("tiers") or []:
            if "narration" in tier:
                raise ValueError(
                    "mechanics.bonds tiers carry 'at' and 'label' only; a 'narration' key is "
                    "refused until a measurement shows labels are not enough (CR-11 decision 2).")

    def scale(self, cfg):
        return DEFAULT_SCALE

    @staticmethod
    def _turn(ctx):
        return ctx["state"].get("pacing", {}).get("turn_count", 0)

    # --- state ---------------------------------------------------------------------

    def init_state(self, cfg, ctx):
        """The seeded bonds, as opened at turn 0. Nothing at all when none are seeded (P-2)."""
        book = {}
        for entry in cfg.get("seed") or []:
            score = max(self.scale(cfg)[0], min(self.scale(cfg)[1], entry["score"]))
            book.setdefault(entry["from"], {})[entry["to"]] = {
                "score": score, "peak": score, "opened_turn": 0}
        return book

    def _room(self, cfg, ctx, key, turn):
        cap = cfg.get("cap_per_window")
        if not cap:
            return None
        span = max(1, int(cap.get("turns", 1)))
        history = ((ctx["state"].get("mechanics") or {}).get(WINDOW_KEY) or {}).get(key) or []
        spent = sum(abs(d) for t, d in history if t > turn - span)
        return max(0, int(cap.get("delta", 0)) - spent)

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        vocabulary = ", ".join(sorted(cfg["registers"]))
        schema = (
            '  "bond_events": [{"from": "<character name>", "to": "<character name>", "event": '
            f'"<exactly one of: {vocabulary}>", "mutual": <true if it went both ways>}}]'
        )
        instruction = (
            "For bond_events, report what one NON-PROTAGONIST character did to or for another in "
            "the NARRATION - from them, to them, which event. Never a magnitude. Use "
            "\"mutual\": true only if each did it to the other. Pick the closest event or omit "
            "it; never invent one. Names must be copied verbatim from EXISTING CHARACTERS when "
            "listed there. [] if nothing passed between two such characters.\n"
        )
        return [ObservationField("bond_events", schema, "", instruction)]

    def events(self, cfg, ctx, diff):
        registers = cfg["registers"]
        out = []
        for entry in diff.get("bond_events") or []:
            if not isinstance(entry, dict):
                continue
            src, dst, event = entry.get("from"), entry.get("to"), entry.get("event")
            if not (isinstance(src, str) and isinstance(dst, str) and src and dst
                    and src != dst and event in registers):
                continue
            out.append({"type": "bond", "from": src, "to": dst, "event": event,
                        "mutual": bool(entry.get("mutual", False))})
        return out

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        low, high = self.scale(cfg)
        turn = self._turn(ctx)
        book = ledger(ctx)
        projected = {(s, d): e.get("score", 0) for s, row in book.items() for d, e in row.items()}
        room = {}
        effects = []
        for event in observations or []:
            if event.get("type") != "bond":
                continue
            delta = int(cfg["registers"][event["event"]])
            pairs = [(event["from"], event["to"])]
            if event.get("mutual"):
                pairs.append((event["to"], event["from"]))
            for pair in pairs:
                key = "|".join(pair)
                if key not in room:
                    room[key] = self._room(cfg, ctx, key, turn)
                applied = delta
                if room[key] is not None:
                    allowed = min(abs(delta), room[key])
                    room[key] -= allowed
                    applied = allowed if delta >= 0 else -allowed
                current = projected.get(pair, 0)
                value = max(low, min(high, current + applied))
                projected[pair] = value
                effects.append(Effect(
                    "bonds.set", reason=f"bond:{event['event']}",
                    source=pair[0], target=pair[1], value=value, delta=value - current,
                    turn=turn, windowed=bool(cfg.get("cap_per_window"))))
        effects.extend(self._evictions(cfg, ctx, projected))
        return effects

    def _evictions(self, cfg, ctx, projected):
        limit = cfg.get("max_generated_bonds", DEFAULT_MAX_GENERATED)
        authored = set((ctx["story"].get("world") or {}).get("characters") or {})
        keep = pinned(ctx)
        generated = [p for p in projected if not (p[0] in authored and p[1] in authored)]
        if len(generated) <= limit:
            return []
        removable = sorted((p for p in generated if p[0] not in keep and p[1] not in keep),
                           key=lambda p: (abs(projected[p]), p))
        return [Effect("bonds.evict", reason="over_max_generated_bonds", source=p[0], target=p[1])
                for p in removable[:len(generated) - limit]]

    # --- prompt ---------------------------------------------------------------------

    def prompt_sections(self, cfg, ctx):
        """`BETWEEN THEM`: tier labels for pairs where both characters are in the scene."""
        book = ledger(ctx)
        present = [n for n in ((ctx["state"].get("plot") or {}).get("current_scene") or {}).get("present") or []
                   if isinstance(n, str)]
        if not book or len(present) < 2:
            return {}
        tiers = cfg.get("tiers") or []
        lines = []
        seen = set()
        for a in sorted(set(present)):
            for b in sorted(set(present)):
                if a == b or frozenset((a, b)) in seen:
                    continue
                seen.add(frozenset((a, b)))
                ab = tier_for(tiers, ((book.get(a) or {}).get(b) or {}).get("score", 0))
                ba = tier_for(tiers, ((book.get(b) or {}).get(a) or {}).get("score", 0))
                if ab is None and ba is None:
                    continue
                if ab is not None and ba is not None and ab["label"] == ba["label"]:
                    lines.append(f"{a} ↔ {b}: {ab['label']}")
                else:
                    bits = [f"{x} → {y}: {t['label']}" for x, y, t in ((a, b, ab), (b, a, ba)) if t]
                    lines.append(" · ".join(bits))
        if not lines:
            return {}
        return {"between_them": "\nBETWEEN THEM:\n" + "\n".join(f"- {line}" for line in lines[:MAX_LINES])}


def _apply_set(ctx, effect):
    p = effect.payload
    book = ctx["state"].setdefault("mechanics", {}).setdefault("bonds", {})
    entry = book.setdefault(p["source"], {}).setdefault(
        p["target"], {"score": 0, "peak": 0, "opened_turn": p["turn"]})
    entry["score"] = p["value"]
    entry["peak"] = max(entry.get("peak", 0), p["value"], key=abs)
    if p["windowed"] and p["delta"]:
        window = ctx["state"]["mechanics"].setdefault(WINDOW_KEY, {})
        history = window.setdefault(f"{p['source']}|{p['target']}", [])
        history.append([p["turn"], p["delta"]])
        window[f"{p['source']}|{p['target']}"] = history[-32:]


def _apply_evict(ctx, effect):
    book = (ctx["state"].get("mechanics") or {}).get("bonds") or {}
    row = book.get(effect.payload["source"])
    if row:
        row.pop(effect.payload["target"], None)
        if not row:
            del book[effect.payload["source"]]


ENGINE = register(ScoredBonds())
register_effect("bonds.set", _apply_set)
register_effect("bonds.evict", _apply_evict)
