"""`lore` / `keyed_lore` - CR-06, triggered lore (Story_Mechanics_Update.md CR-06).

Facts that only matter while a character or place is on the page, kept out of `world.rules` and
injected under `LORE:` when something triggers them. At most `max_active` entries reach the
prompt, highest `priority` first (ties by authored order), so the cost is bounded by
`max_active` x entry length however much lore a story authors.

Triggers, for one turn: a key found (case-insensitive, whole word or phrase) in the player's
action or the last narration; or `also_when`. An entry with an `unlock` condition stays dormant
until it holds. `sticky_turns` keeps an entry in for N turns after the turn it last triggered.

`also_when` and `unlock` are evaluated CLOSED, the same as the sample bar's preview
(`author_evaluate.lore_injection`): a typo costs a missing paragraph of lore, never lore the
author staged for later showing up early.

No observation field, no effects, no LLM call: this engine reads the page and the state. The
only thing it writes is `state.mechanics.lore.hit` (entry id -> last turn it triggered), which
is what stickiness reads, recorded through `touch()` when the prompt is built.
"""
import re

from . import MechanicEngine, register

DEFAULT_MAX_ACTIVE = 3
HEADER = "LORE:\n"
# max_active x the longest entries, plus the header. check_config refuses a story whose
# worst case does not fit, so exceeding it is a load-time error and not a turn-time one.
PROMPT_BUDGET = 3000


def _turn(ctx):
    return ctx["state"].get("pacing", {}).get("turn_count", 0)


def _hits(ctx):
    return ((ctx["state"].get("mechanics") or {}).get("lore") or {}).get("hit") or {}


def page_text(ctx):
    """The player's action plus the last narration - the text keys are matched against."""
    recent = (ctx["state"].get("history") or {}).get("recent_turns") or []
    last = recent[-1].partition("\nNarrator:")[2] if recent else ""
    return f"{ctx.get('player_action') or ''}\n{last or ''}"


def key_hit(entry, text):
    for key in entry.get("keys") or []:
        if isinstance(key, str) and key.strip() and re.search(
                rf"(?<!\w){re.escape(key.strip())}(?!\w)", text, re.IGNORECASE):
            return True
    return False


def select(cfg, ctx):
    """`[(entry, triggered_now)]` in injection order, at most `max_active` of them."""
    entries = [e for e in cfg.get("entries") or [] if isinstance(e, dict) and e.get("id")]
    text = page_text(ctx)
    turn = _turn(ctx)
    hits = _hits(ctx)
    import conditions
    holds = lambda cond: conditions.satisfied(cond, ctx, conditions.CLOSED)  # noqa: E731
    live = []
    for index, entry in enumerate(entries):
        if entry.get("unlock") is not None and not holds(entry["unlock"]):
            continue
        triggered = key_hit(entry, text) or (entry.get("also_when") is not None and holds(entry["also_when"]))
        sticky = (not triggered and entry["id"] in hits
                  and turn - hits[entry["id"]] <= int(entry.get("sticky_turns") or 0))
        if triggered or sticky:
            priority = entry.get("priority") if isinstance(entry.get("priority"), (int, float)) else 0
            live.append((-priority, index, entry, triggered))
    live.sort(key=lambda t: t[:2])
    limit = cfg.get("max_active") or DEFAULT_MAX_ACTIVE
    return [(entry, triggered) for _, _, entry, triggered in live[:limit]]


def touch(cfg, ctx):
    """Record the turn each injected entry triggered, so `sticky_turns` has something to read.
    Idempotent within a turn, which matters because the prompt is assembled section by section."""
    turn = _turn(ctx)
    chosen = [e["id"] for e, triggered in select(cfg, ctx) if triggered]
    if chosen:
        hit = ctx["state"].setdefault("mechanics", {}).setdefault("lore", {}).setdefault("hit", {})
        for entry_id in chosen:
            hit[entry_id] = turn


class KeyedLore(MechanicEngine):
    slot = "lore"
    name = "keyed_lore"
    prompt_budget = PROMPT_BUDGET

    def check_config(self, cfg):
        entries = [e for e in cfg.get("entries") or [] if isinstance(e, dict)]
        if not entries:
            raise ValueError("mechanics.lore declares engine 'keyed_lore' but authors no entries.")
        ids = [e.get("id") for e in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("mechanics.lore authors the same entry id twice.")
        limit = cfg.get("max_active") or DEFAULT_MAX_ACTIVE
        worst = len(HEADER) + sum(sorted((len(e.get("content", "")) + 3 for e in entries), reverse=True)[:limit])
        if worst > PROMPT_BUDGET:
            raise ValueError(
                f"mechanics.lore: its {limit} longest entries come to {worst} characters, over the "
                f"{PROMPT_BUDGET}-character budget for injected lore. Shorten them or lower max_active.")

    def init_state(self, cfg, ctx):
        return {"hit": {}}

    def prompt_sections(self, cfg, ctx):
        chosen = select(cfg, ctx)
        if not chosen:
            return {}
        return {"entries": "\n" + HEADER + "\n".join(f"- {e['content']}" for e, _ in chosen)}

    def resolve(self, cfg, ctx, observations, events):
        return []


ENGINE = register(KeyedLore())
