"""`side_threads` / `episodic_threads` - CR-11, side threads and vignettes (Story_Mechanics_Update.md).

A separate track of short episodes the story has between its planned beats. The engine decides
**when** and **who** from state (P-7); the model only writes **what happens**. A side thread is
never in `plot.subplots`, never counts toward act advancement, and has no structural output.

What lives here (pure, no I/O): the recipe/cast binding, the offer the narrator gets, the one
observation field, the end rules, and the vignette picker. What does not: the single generation
call, which `story_engine.generate_side_thread` makes with the prompt built here.

Lifecycle, each part in code rather than in a prompt:
  start   after a turn whose beat is in `start_after_beats`, with room (`max_active`), past the
          cooldown, and no ending committed. Deterministic: casts not used by the last three
          concluded threads, then the largest |bond| the eligibility names, then recipe order.
  run     one optional line a turn, only when no pacing rule is armed and no drive nudge is
          active; rotated across threads. The one observation field answers it.
  end     `resolves_when` / `fails_when` evaluated CLOSED (D2) every turn, a `detect_hit`, and
          the `max_turns` backstop: a closing line is added whether or not a rule is armed, and
          the thread concludes the turn after. Every thread ends; the engine keeps that promise.
  ending  no new thread once an ending commits; live threads get a wrap-up line and conclude
          `finale` at the end of the finale's first turn.

Tier C: the observation pass already runs there, and the generation call is extraction too -
it fills a small form from a premise and a cast, with the cast and the allowed moves fixed by
code, so a miss costs a duller episode and never a wrong state. (CLAUDE.md: an engine call is
Tier C unless the module records why not.)

State, `state.mechanics.side_threads`: `active`, `concluded` (grows on disk, never prompted past
the last three), `last_started_turn`, `next_id`, `offered`, and - only when `vignettes` is
authored - `last_vignette_turn`, `vignette_cursor` and `vignette_subjects`.
"""
import itertools

import conditions

from . import Effect, MechanicEngine, ObservationField, bound_for, register, register_effect

PURSUIT_RECIPE = "player_pursuit"
DEFAULT_RECIPE = "default"
DEFAULT_MAX_ACTIVE = 1
DEFAULT_MAX_TURNS = 30
RECENT_CONCLUDED = 3
RECENT_SEEN_TURNS = 5
MAX_CANDIDATES = 8
MAX_BINDINGS = 240
VIGNETTE_MEMORY = 8
PREMISE_WORDS = 40
MAX_BEATS = 4
TITLE_CHARS = 80
BEAT_CHARS = 220
OUTCOMES = ("resolved", "failed", "expired", "finale", "abandoned")


# --- reading state -------------------------------------------------------------------------

def block(ctx):
    return (ctx["state"].get("mechanics") or {}).get("side_threads") or {}


def _turn(ctx):
    return ctx["state"].get("pacing", {}).get("turn_count", 0)


def _authoring(ctx):
    return (((ctx.get("authoring") or {}).get("mechanics") or {}).get("side_threads")) or {}


def protected(cfg, ctx):
    """Authored characters no thread casts. `protected` is author-visibility, so the engine view
    of the block does not carry it; code (never a prompt) reads it from the author half."""
    return set(_authoring(ctx).get("protected") or cfg.get("protected") or [])


def recipes(cfg, ctx):
    """Authored recipes with their ids. Ids are author-visibility too, so they are re-attached
    from the author half by position; nothing built from them reaches a prompt."""
    extra = _authoring(ctx).get("recipes") or []
    out = []
    for i, r in enumerate(cfg.get("recipes") or []):
        rid = r.get("id")
        if not rid and i < len(extra) and isinstance(extra[i], dict):
            rid = extra[i].get("id")
        out.append({**r, "id": rid or f"recipe_{i + 1}"})
    return out


def committed(ctx):
    ending = (ctx["state"].get("mechanics") or {}).get("endings") or {}
    return bool(ending.get("committed") or ctx["state"]["plot"]["endgame"]["requested"])


def active(ctx):
    return list(block(ctx).get("active") or [])


def concluded(ctx):
    return list(block(ctx).get("concluded") or [])


def player_cfg(cfg):
    """`player_threads` (CR-12), or None: absent means the feature does not exist (P-2)."""
    pt = cfg.get("player_threads")
    return pt if isinstance(pt, dict) else None


def player_live(ctx):
    return [t for t in active(ctx) if t.get("origin") == "player"]


def player_room(cfg, ctx):
    pt = player_cfg(cfg)
    return pt is not None and len(player_live(ctx)) < int(pt.get("max_active") or 1)


def pursuit(ctx):
    return block(ctx).get("pursuit")


def max_turns(cfg):
    return int(cfg.get("max_turns") or DEFAULT_MAX_TURNS)


def age(thread, ctx):
    return _turn(ctx) - thread.get("started_turn", 0)


def _characters(ctx):
    """(authored, discovered) names, both in a stable order."""
    authored = list((ctx["story"].get("world") or {}).get("characters") or {})
    found = sorted(n for n in (ctx["state"].get("characters") or {}) if n not in authored)
    return authored, found


def _characters_all(ctx):
    authored, found = _characters(ctx)
    return authored + found


def _cast_names(thread):
    return [v for v in (thread.get("cast") or {}).values() if isinstance(v, str)]


def _held_items(ctx):
    out = []
    for entry in ctx["state"].get("protagonist", {}).get("inventory") or []:
        if isinstance(entry, str):
            out.append((entry, []))
        elif isinstance(entry, dict):
            out.append((entry.get("label") or entry.get("name") or "", list(entry.get("tags") or [])))
    return [(label, tags) for label, tags in out if label]


def _bond_score(ctx, src, dst):
    book = (ctx["state"].get("mechanics") or {}).get("bonds") or {}
    return ((book.get(src) or {}).get(dst) or {}).get("score", 0)


# --- conditions ----------------------------------------------------------------------------

def _bond_weight(cond, ctx):
    """Total |score| of every bond a (bound) condition names - how much the story has already
    invested in the pair, which is what ranks one eligible binding over another."""
    if isinstance(cond, list):
        return sum(_bond_weight(c, ctx) for c in cond)
    if not isinstance(cond, dict):
        return 0
    total = 0
    for key, value in cond.items():
        if key == "bond" and isinstance(value, list) and len(value) == 2:
            total += abs(_bond_score(ctx, value[0], value[1]))
        elif isinstance(value, (dict, list)):
            total += _bond_weight(value, ctx)
    return total


def confine(cond, may_move, cast):
    """`cond` if every leaf in it reads only something `may_move` allows, else None. The engine
    nulls a generated condition that strays; it never trusts the model with this (CR-11)."""
    cond = conditions.normalize(cond)
    if not isinstance(cond, dict) or not cond:
        return None
    allowed = set(may_move)
    for part in conditions._parts(cond):  # noqa: SLF001 - the one decomposition of a condition
        kind = part[0]
        if kind == "group":
            _, key, value = part
            children = value if key in ("all", "any") else [value]
            if not isinstance(children, list) or not children:
                return None
            if any(confine(c, may_move, cast) is None for c in children):
                return None
        elif kind == "bond":
            pair = part[1].get("pair")
            if not (isinstance(pair, list) and len(pair) == 2
                    and all(p in cast for p in pair) and f"bond:{pair[0]},{pair[1]}" in allowed):
                return None
        elif kind == "relationship":
            if f"relationship:{part[1].get('name')}" not in allowed or part[1].get("name") not in cast:
                return None
        elif kind == "stat":
            if f"stat:{part[1].get('axis')}" not in allowed:
                return None
        elif kind == "leaf" and part[1] == "item_tag":
            if f"item:{part[2]}" not in allowed:
                return None
        elif kind == "leaf" and part[1] == "leverage_kind":
            if f"leverage:{part[2]}" not in allowed:
                return None
        else:
            return None
    return cond


def _holds(cond, ctx):
    return bool(cond) and conditions.satisfied(cond, ctx, conditions.CLOSED)


# --- binding -------------------------------------------------------------------------------

def _pool(spec, ctx, prot, busy):
    authored, found = _characters(ctx)
    src = spec.get("from", "any")
    if src == "any":
        pool = authored + found
    elif src == "authored":
        pool = authored
    elif src == "generated":
        pool = found
    else:
        pool = [src] if src in authored else []
    return [n for n in pool if n not in prot and n not in busy][:MAX_CANDIDATES]


def _followed_candidates(recipe, ctx):
    """Concluded threads this recipe may follow, most recent first. Each concluded thread can be
    followed once (`followed_by`), and only after `min_turns_since` turns."""
    follows = recipe.get("follows")
    if not isinstance(follows, dict):
        return [None]
    want = follows.get("recipe", "any")
    outcomes = follows.get("outcome")
    gap = follows.get("min_turns_since", 0)
    out = []
    for done in reversed(concluded(ctx)):
        if done.get("followed_by"):
            continue
        if want != "any" and done.get("recipe") != want:
            continue
        if outcomes and done.get("outcome") not in outcomes:
            continue
        if _turn(ctx) - (done.get("turns") or [0, 0])[1] < gap:
            continue
        out.append(done)
    return out


def bindings(recipe, ctx, prot, busy):
    """Every way `recipe`'s cast can be filled, as `{slot: value}` dicts paired with the thread
    followed (or None). Bounded: MAX_CANDIDATES a slot, MAX_BINDINGS overall."""
    cast = recipe.get("cast") or {}
    authored, found = _characters(ctx)
    alive = set(authored) | set(found)
    locations = (ctx["story"].get("world") or {}).get("locations") or {}
    out = []
    for followed in _followed_candidates(recipe, ctx):
        slots, pools = [], []
        for slot, spec in cast.items():
            kind = spec.get("kind", "character")
            if kind == "character" and spec.get("from") == "followed":
                name = ((followed or {}).get("cast") or {}).get(spec.get("slot"))
                pool = [name] if name in alive and name not in prot and name not in busy else []
            elif kind == "character":
                pool = _pool(spec, ctx, prot, busy)
            elif kind == "location":
                pool = [spec["id"]] if spec.get("id") in locations else []
            else:
                pool = sorted({label for label, tags in _held_items(ctx) if spec.get("tag") in tags})[:MAX_CANDIDATES]
            slots.append(slot)
            pools.append(pool)
        if not slots or any(not p for p in pools):
            continue
        for combo in itertools.product(*pools):
            chars = [v for s, v in zip(slots, combo) if cast[s].get("kind", "character") == "character"]
            if len(set(chars)) != len(chars):
                continue
            out.append((dict(zip(slots, combo)), followed))
            if len(out) >= MAX_BINDINGS:
                return out
    return out


def _seen_recently(ctx):
    texts = (ctx["state"].get("history") or {}).get("recent_turns") or []
    blob = "\n".join(texts[-RECENT_SEEN_TURNS:])
    authored, found = _characters(ctx)
    return [n for n in authored + found if n in blob]


def _default_binding(cfg, ctx, prot, busy):
    """The built-in recipe: the directed pair with the largest |score| among characters seen in
    the last few turns. Needs bonds to choose by, and a pair with no score is no pair."""
    if bound_for(ctx["story"], "bonds") is None:
        return None
    seen = [n for n in _seen_recently(ctx) if n not in prot and n not in busy]
    best = None
    for a, b in itertools.permutations(seen, 2):
        weight = abs(_bond_score(ctx, a, b))
        if weight and (best is None or weight > best[0]):
            best = (weight, a, b)
    if best is None:
        return None
    moves = ["bond:a,b", "bond:b,a"]
    if bound_for(ctx["story"], "relationships") is not None:
        moves += ["relationship:a", "relationship:b"]
    return {
        "recipe": DEFAULT_RECIPE,
        "cast": {"a": best[1], "b": best[2]},
        "followed": None,
        "spec": {"id": DEFAULT_RECIPE,
                 "premise": "something between them comes to a head in an ordinary moment",
                 "may_move": moves, "may_create_npc": False,
                 "cast": {"a": {"from": "any"}, "b": {"from": "any"}}},
    }


def candidates(cfg, ctx):
    """Every eligible authored (recipe, cast) binding, ranked in the order they would be picked."""
    prot = protected(cfg, ctx)
    busy = {n for t in active(ctx) for n in _cast_names(t)}
    recent = [frozenset(_cast_names(t)) for t in concluded(ctx)[-RECENT_CONCLUDED:]]
    ranked = []
    for order, recipe in enumerate(recipes(cfg, ctx)):
        for cast, followed in bindings(recipe, ctx, prot, busy):
            bound_cond = conditions.bind_slots(recipe.get("eligible_when") or {}, cast)
            if bound_cond and not _holds(bound_cond, ctx):
                continue
            used = frozenset(v for v in cast.values() if isinstance(v, str)) in recent
            ranked.append(((used, -_bond_weight(bound_cond, ctx), order),
                           {"recipe": recipe["id"], "cast": cast,
                            "followed": (followed or {}).get("id"), "spec": recipe}))
    ranked.sort(key=lambda r: r[0])
    return [b for _, b in ranked]


def choose(cfg, ctx):
    found = candidates(cfg, ctx)
    if found:
        return found[0]
    if cfg.get("default_recipe") is not False:
        return _default_binding(cfg, ctx, protected(cfg, ctx),
                                {n for t in active(ctx) for n in _cast_names(t)})
    return None


def pursuit_binding(cfg, summary, cast):
    """The synthetic `player_pursuit` recipe (CR-12): the confirmed summary as the premise, the
    characters in the reporting scenes as the cast, and `may_move` from `player_threads`.
    `relationship:cast` expands to one `relationship:<slot>` per cast member."""
    slots = {f"c{i + 1}": name for i, name in enumerate(cast)}
    moves = []
    for m in (player_cfg(cfg) or {}).get("may_move") or []:
        if m == "relationship:cast":
            moves += [f"relationship:{slot}" for slot in slots]
        else:
            moves.append(m)
    return {"recipe": PURSUIT_RECIPE, "cast": slots, "followed": None, "origin": "player",
            "spec": {"id": PURSUIT_RECIPE, "premise": summary, "may_move": moves, "may_create_npc": False,
                     "cast": {slot: {"from": "any"} for slot in slots}}}


# --- starting ------------------------------------------------------------------------------

def _live_engine_threads(ctx):
    return [t for t in active(ctx) if t.get("origin", "engine") != "player"]


def start_due(cfg, ctx):
    """Whether a thread may start this turn. Every condition is checked in code."""
    if committed(ctx):
        return False
    beat = (ctx["state"].get("pacing", {}).get("last_beat") or {}).get("type")
    if beat not in (cfg.get("start_after_beats") or []):
        return False
    if len(_live_engine_threads(ctx)) >= int(cfg.get("max_active") or DEFAULT_MAX_ACTIVE):
        return False
    last = block(ctx).get("last_started_turn")
    return last is None or _turn(ctx) - last >= int(cfg.get("cooldown_turns") or 0)


def _cast_details(ctx, cast_spec, cast):
    lines = []
    story_chars = (ctx["story"].get("world") or {}).get("characters") or {}
    found = ctx["state"].get("characters") or {}
    locations = (ctx["story"].get("world") or {}).get("locations") or {}
    for slot, value in cast.items():
        kind = (cast_spec.get(slot) or {}).get("kind", "character")
        if kind == "character":
            rec = {**(found.get(value) or {}), **(story_chars.get(value) or {})}
            bits = [rec.get("description", ""), rec.get("first_contact", "")]
            lines.append(f"- {slot}: {value} - " + " ".join(b for b in bits if b))
        elif kind == "location":
            loc = locations.get(value) or {}
            lines.append(f"- {slot}: {loc.get('name', value)} - {loc.get('description', '')}")
        else:
            lines.append(f"- {slot}: {value} (an item the protagonist holds)")
    return "\n".join(lines)


def _bond_lines(ctx, cast):
    block_cfg = (ctx["story"].get("mechanics") or {}).get("bonds") or {}
    if not block_cfg:
        return ""
    from . import bonds
    names = [(s, v) for s, v in cast.items() if isinstance(v, str)]
    out = []
    for (sa, a), (sb, b) in itertools.permutations(names, 2):
        tier = bonds.tier_for(block_cfg.get("tiers"), _bond_score(ctx, a, b))
        if tier:
            out.append(f"- {sa} toward {sb}: {tier['label']}")
    return "\n".join(out)


def _grammar(spec):
    allowed = spec.get("may_move") or []
    lines = []
    if any(m.startswith("bond:") for m in allowed):
        pairs = ", ".join(m[5:] for m in allowed if m.startswith("bond:"))
        lines.append(f'- {{"bond": ["<slot>", "<slot>"], "gte" or "lte": <number -100..100>}} for these ordered pairs: {pairs}')
    if any(m.startswith("relationship:") for m in allowed):
        who = ", ".join(m[13:] for m in allowed if m.startswith("relationship:"))
        lines.append(f'- {{"relationship": "<slot>", "gte" or "lte": <number>}} for: {who}')
    for prefix, key, label in (("stat:", "stat", "axis"), ("item:", "item_tag", "tag"), ("leverage:", "leverage_kind", "kind")):
        vals = [m[len(prefix):] for m in allowed if m.startswith(prefix)]
        if vals:
            comparator = ', "gte" or "lte": <number>' if key == "stat" else ""
            lines.append(f'- {{"{key}": "<{label}>"{comparator}}} for: {", ".join(vals)}')
    return "\n".join(lines)


def generation_prompt(cfg, ctx, binding, extra=""):
    """The one prompt the generation call gets. Built only from narrator-visible material plus the
    recipe's own premise: never canon, judge text, ending names, waypoints or flags (CR-03)."""
    spec, cast = binding["spec"], binding["cast"]
    rules = (ctx["story"].get("world") or {}).get("rules") or []
    recent = [t.get("title", "") for t in concluded(ctx)[-RECENT_CONCLUDED:] if t.get("title")]
    followed = next((t for t in concluded(ctx) if t.get("id") == binding.get("followed")), None)
    authored, found = _characters(ctx)
    parts = [
        "Write a short self-contained episode (a side thread) for an ongoing interactive story. "
        "It is not part of the main plot and must not decide it.",
        "WORLD RULES:\n" + "\n".join(f"- {r}" for r in rules),
        f"PREMISE: {spec.get('premise', '')}",
        "CAST:\n" + _cast_details(ctx, spec.get("cast") or {}, cast),
    ]
    bond_text = _bond_lines(ctx, cast)
    if bond_text:
        parts.append("HOW THEY STAND:\n" + bond_text)
    if followed:
        parts.append(f"THIS FOLLOWS AN EARLIER EPISODE - '{followed.get('title', '')}': "
                     f"{followed.get('premise', '')} (it ended {followed.get('outcome', '')}).")
    if extra:
        parts.append(extra)
    parts.append("RECENT EPISODES (do not repeat): " + (", ".join(recent) or "none"))
    parts.append("EXISTING CHARACTERS (do not repeat): " + (", ".join(authored + found) or "none"))
    grammar = _grammar(spec)
    npc = ('"new_character": null or {"name": "<full name>", "description": "...", "role": "...", '
           '"first_contact": "...", "hook": "..."}' if spec.get("may_create_npc") else '"new_character": null')
    parts.append(
        "Respond with ONLY a JSON object:\n{\n"
        '  "title": "<short title>",\n'
        f'  "premise": "<at most {PREMISE_WORDS} words, what the episode is about>",\n'
        f'  "beats": ["<2-{MAX_BEATS} short beats, in order>"],\n'
        '  "resolves_when": <a condition, or null>,\n'
        '  "fails_when": <a condition, or null>,\n'
        '  "detect": "<one sentence a judge could check against a scene: when has this episode resolved?>",\n'
        f"  {npc}\n}}\n"
        "Conditions refer to cast members by their SLOT name and may use ONLY these forms, combined "
        "with \"all\", \"any\" or \"not\"; use null if none fits:\n" + (grammar or "(none: write null)")
    )
    return "\n\n".join(p for p in parts if p)


def build_thread(cfg, ctx, binding, generated, origin=None):
    """A thread dict from a binding and what the model returned, or None when it returned nothing
    usable. Conditions that stray outside `may_move` are nulled, not trusted."""
    if not isinstance(generated, dict):
        return None
    title = str(generated.get("title") or "").strip()[:TITLE_CHARS]
    if not title:
        return None
    spec, cast = binding["spec"], binding["cast"]
    premise = " ".join(str(generated.get("premise") or spec.get("premise") or "").split()[:PREMISE_WORDS])
    beats = [str(b).strip()[:BEAT_CHARS] for b in (generated.get("beats") or []) if str(b).strip()][:MAX_BEATS]
    if not beats:
        beats = [premise or title]
    moves = spec.get("may_move") or []
    conds = {}
    for key in ("resolves_when", "fails_when"):
        safe = confine(generated.get(key), moves, cast)
        conds[key] = conditions.bind_slots(safe, cast) if safe else None
    origin = origin or binding.get("origin", "engine")
    turn = _turn(ctx)
    next_id = int(block(ctx).get("next_id") or 1)
    return {
        "id": f"st_{next_id:03d}", "recipe": binding["recipe"], "origin": origin,
        "cast": dict(cast), "title": title, "premise": premise, "beats": beats, "beat_index": 0,
        "resolves_when": conds["resolves_when"], "fails_when": conds["fails_when"],
        "detect": str(generated.get("detect") or "").strip(), "started_turn": turn,
        "max_turns": max_turns(cfg), "last_offered_turn": None, "followed": binding.get("followed"),
    }


# --- running -------------------------------------------------------------------------------

def _armed(ctx):
    return bool(ctx["state"].get("pacing", {}).get("armed"))


def _drive_active(ctx):
    endings = bound_for(ctx["story"], "endings")
    if endings is None:
        return False
    return bool(endings.engine.nudge_plan(endings.cfg, ctx).get("drive"))


def _rotation(threads):
    return sorted(threads, key=lambda t: (t.get("last_offered_turn") is not None,
                                          t.get("last_offered_turn") or 0, t["started_turn"], t["id"]))


def _vignette_subject(cfg, ctx):
    spec = cfg.get("vignettes") or {}
    st = block(ctx)
    n = int(st.get("vignette_cursor") or 0)
    seeds = [s for s in spec.get("seeds") or [] if isinstance(s, str) and s.strip()]
    kinds = spec.get("subjects") or ["location", "character", "item"]
    pool = []
    prot = protected(cfg, ctx)
    if "location" in kinds:
        pool += [(loc.get("name") or lid) for lid, loc in sorted(((ctx["story"].get("world") or {}).get("locations") or {}).items())]
    if "character" in kinds:
        authored, found = _characters(ctx)
        pool += [n_ for n_ in authored + found if n_ not in prot]
    if "item" in kinds:
        pool += [label for label, _ in _held_items(ctx)]
    use_seed = bool(seeds) and (n % 2 == 0 or not pool)
    if use_seed:
        return seeds[(n // 2) % len(seeds)]
    if not pool:
        return None
    memory = list(st.get("vignette_subjects") or [])
    return min(pool, key=lambda s: (memory.index(s) if s in memory else -1))


def choose_offer(cfg, ctx):
    """What the narrator is offered this turn, as `{"kind", "id"?, "subject"?}`, or None. Pure."""
    live = active(ctx)
    due = [t for t in live if age(t, ctx) >= t.get("max_turns", max_turns(cfg))]
    if due:  # the closing line is the engine's promise, so it ignores the rule and drive checks
        return {"kind": "closing", "id": due[0]["id"]}
    if committed(ctx):
        wrapped = _rotation(live)
        return {"kind": "wrapup", "id": wrapped[0]["id"]} if wrapped and not _armed(ctx) else None
    quiet = not _armed(ctx) and not _drive_active(ctx)
    if live and quiet:
        order = _rotation(live)
        order.sort(key=lambda t: t.get("origin") != "player")
        return {"kind": "thread", "id": order[0]["id"]}
    vignettes = cfg.get("vignettes")
    if vignettes and quiet and not live:
        last = block(ctx).get("last_vignette_turn")
        if (last is None and _turn(ctx) >= int(vignettes.get("every") or 1)) or (
                last is not None and _turn(ctx) - last >= int(vignettes.get("every") or 1)):
            subject = _vignette_subject(cfg, ctx)
            if subject:
                return {"kind": "texture", "subject": subject}
    return None


def current_offer(cfg, ctx):
    """The offer recorded for this prompt, else a fresh pure choice - so the prompt and the
    observation pass agree even though the turn counter moves between them."""
    recorded = block(ctx).get("offered")
    if recorded and recorded.get("turn") == _turn(ctx):
        return recorded.get("offer")
    return choose_offer(cfg, ctx)


def record_offer(cfg, ctx):
    """Called once when the narration prompt is built. Written to state so the observation pass,
    which runs after the turn counter moved, asks about the thread the narrator was offered."""
    offer = choose_offer(cfg, ctx)
    st = ctx["state"].setdefault("mechanics", {}).setdefault("side_threads", {})
    st["offered"] = {"turn": _turn(ctx), "offer": offer}
    return offer


def _find(ctx, tid):
    return next((t for t in active(ctx) if t.get("id") == tid), None)


def _line(offer, ctx):
    if offer["kind"] == "texture":
        return f"TEXTURE, if the scene has room: {offer['subject']}"
    thread = _find(ctx, offer["id"])
    if thread is None:
        return None
    title = thread["title"]
    beat = thread["beats"][min(thread.get("beat_index", 0), len(thread["beats"]) - 1)]
    if offer["kind"] == "closing":
        return f"SIDE THREAD, bring it to a close in this scene: {title}: {beat}"
    if offer["kind"] == "wrapup":
        return f"SIDE THREAD, let it settle gently, the story is nearly over: {title}"
    return f"SIDE THREAD, if it fits this scene: {title}: {beat}"


# --- the engine ----------------------------------------------------------------------------

class EpisodicThreads(MechanicEngine):
    slot = "side_threads"
    name = "episodic_threads"
    # After relationships (30), bonds (31) and inventory (40): the end rules read this turn's
    # scores, and the progress events carry no price of their own.
    resolve_order = 60
    prompt_budget = 600

    def check_config(self, cfg):
        starts = cfg.get("start_after_beats")
        if not isinstance(starts, list) or not starts:
            raise ValueError(
                "mechanics.side_threads declares engine 'episodic_threads' but authors no "
                "'start_after_beats'. Which beat is the story's breathing room is a creative "
                "decision, so there is no default (CR-11); name at least one pacing-loop beat.")

    def init_state(self, cfg, ctx):
        state = {"active": [], "concluded": [], "last_started_turn": None, "next_id": 1, "offered": None}
        if player_cfg(cfg):
            state["pursuit"] = None
        if cfg.get("vignettes"):
            state.update(last_vignette_turn=None, vignette_cursor=0, vignette_subjects=[])
        return state

    # --- observation ---------------------------------------------------------------

    def _offered_thread(self, cfg, ctx):
        recorded = block(ctx).get("offered")
        if not recorded or _turn(ctx) - recorded.get("turn", -9) not in (0, 1):
            return None
        offer = recorded.get("offer") or {}
        return _find(ctx, offer.get("id")) if offer.get("kind") in ("thread", "closing", "wrapup") else None

    def observations(self, cfg, ctx):
        thread = self._offered_thread(cfg, ctx)
        pt = player_cfg(cfg)
        if thread is None and pt is None:
            return None
        offered_schema = "[]"
        context, instruction = "", ""
        if thread is not None:
            detect = thread.get("detect") or "the episode has plainly reached its end"
            offered_schema = (f'[{{"id": "{thread["id"]}", "advanced": <true if this scene moved the episode '
                              f'forward>, "detect_hit": <true only if: {detect}>}}]')
            context = f'SIDE THREAD OFFERED THIS SCENE ({thread["id"]}): {thread["title"]}\n'
            instruction = (
                "For side_thread_progress, report only whether the scene actually took up the offered "
                "side thread. \"advanced\" is false if the narration ignored it. [] if it was ignored.\n")
        if pt is None:
            return [ObservationField("side_thread_progress", f'  "side_thread_progress": {offered_schema}',
                                     context.strip("\n"), instruction)]
        cand = pursuit(ctx)
        titles = [t["title"] for t in active(ctx)] + [
            sp.get("title", "") for sp in (ctx["state"].get("plot", {}).get("subplots") or {}).values()
            if sp.get("status") == "active" and sp.get("title")]
        context += ("CURRENT PURSUIT CANDIDATE: " + (cand["summary"] if cand else "none") + "\n"
                    "ALREADY RUNNING (a pursuit matching one of these is not new): " + ("; ".join(titles) or "none"))
        instruction += (
            "For the pursuit key, report something the PROTAGONIST chose to do for its own sake, off the "
            "main plot, that the scene shows them committing to (an errand, a project, a favour) - and "
            "only if it matches nothing in ALREADY RUNNING. \"summary\" is one short sentence; "
            "\"continues\" is true only if it is the same pursuit as the candidate; \"with\" lists the "
            "named characters it involves. null if there is none.\n")
        schema = ('  "side_thread_progress": {"offered": ' + offered_schema + ', "pursuit": null or '
                  '{"summary": "<sentence>", "continues": <true|false>, "with": ["<character name>"]}}')
        return [ObservationField("side_thread_progress", schema, context.strip("\n"), instruction)]

    def events(self, cfg, ctx, diff):
        thread = self._offered_thread(cfg, ctx)
        raw = diff.get("side_thread_progress")
        pt = player_cfg(cfg)
        offered, pursued = (raw.get("offered"), raw.get("pursuit")) if isinstance(raw, dict) else (raw, None)
        out = []
        for entry in offered if isinstance(offered, list) else []:
            if thread and isinstance(entry, dict) and entry.get("id") == thread["id"]:
                out.append({"type": "side_progress", "id": thread["id"],
                            "advanced": bool(entry.get("advanced")),
                            "detect_hit": bool(entry.get("detect_hit"))})
        if pt is not None and isinstance(pursued, dict) and str(pursued.get("summary") or "").strip():
            known = set(_characters_all(ctx))
            who = [n for n in (pursued.get("with") or []) if isinstance(n, str) and n in known]
            out.append({"type": "side_pursuit", "summary": " ".join(str(pursued["summary"]).split())[:160],
                        "continues": bool(pursued.get("continues")), "with": who})
        return out

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        effects = []
        offered = (block(ctx).get("offered") or {})
        offer = offered.get("offer") or {}
        turn = _turn(ctx)
        hit = {e["id"]: e for e in observations or [] if e.get("type") == "side_progress"}
        for tid, e in hit.items():
            thread = _find(ctx, tid)
            if thread is None:
                continue
            index = thread.get("beat_index", 0)
            if e["advanced"]:
                index = min(index + 1, len(thread["beats"]) - 1)
            effects.append(Effect("side.progress", reason="observed", id=tid, beat_index=index,
                                  detected=bool(e["detect_hit"]), turn=turn,
                                  unadvanced=0 if e["advanced"] else thread.get("unadvanced", 0) + 1))
        if offer.get("kind") in ("thread", "closing", "wrapup") and offer.get("id") not in hit:
            current = _find(ctx, offer["id"]) or {}
            effects.append(Effect("side.progress", reason="offered", id=offer["id"],
                                  beat_index=current.get("beat_index", 0), detected=False, turn=turn,
                                  unadvanced=current.get("unadvanced", 0) + 1))
        elif offer.get("kind") == "texture":
            effects.append(Effect("side.vignette", reason="texture", subject=offer["subject"], turn=turn))
        effects.extend(self._pursuit(cfg, ctx, observations or [], turn))
        return effects

    def _pursuit(self, cfg, ctx, observations, turn):
        """CR-12 confirmation, in code: a pursuit becomes a thread only once it has been reported
        `confirm.reports` times within `confirm.within_turns`. A new summary restarts the count."""
        pt = player_cfg(cfg)
        if pt is None:
            return []
        reports = [e for e in observations if e.get("type") == "side_pursuit"]
        if not reports:
            return []
        e = reports[-1]
        cand = pursuit(ctx)
        window = int((pt.get("confirm") or {}).get("within_turns") or 5)
        need = max(2, int((pt.get("confirm") or {}).get("reports") or 2))
        if cand and e["continues"] and not cand.get("ready"):
            history = [t for t in cand.get("reports", []) if t > turn - window] + [turn]
            who = sorted(set(cand.get("cast", [])) | set(e["with"]))
            summary = cand["summary"]
        else:
            history, who, summary = [turn], sorted(set(e["with"])), e["summary"]
        if protected(cfg, ctx) & set(who):
            return [Effect("side.pursuit", reason="protected", candidate=None)]
        if len(history) >= need and player_room(cfg, ctx):
            return [Effect("side.pursuit", reason="confirmed", candidate={
                "summary": summary, "reports": history, "cast": who, "ready": True})]
        return [Effect("side.pursuit", reason="reported", candidate={
            "summary": summary, "reports": history, "cast": who, "ready": False})]

    def settle(self, cfg, ctx):
        """End rules, evaluated after the turn's effects landed. Returns conclusion effects."""
        effects = []
        turn = _turn(ctx)
        ending = committed(ctx)
        for thread in active(ctx):
            outcome = None
            if ending:
                if thread.get("wrap_from") is None:
                    effects.append(Effect("side.wrap", reason="ending_committed", id=thread["id"], turn=turn))
                elif thread["wrap_from"] < turn:
                    outcome = "finale"
            elif _holds(thread.get("resolves_when"), ctx):
                outcome = "resolved"
            elif _holds(thread.get("fails_when"), ctx):
                outcome = "failed"
            elif thread.get("detected"):
                outcome = "resolved"
            elif (thread.get("origin") == "player" and player_cfg(cfg) and player_cfg(cfg).get("abandon_after_offers")
                  and thread.get("unadvanced", 0) >= int(player_cfg(cfg)["abandon_after_offers"])):
                outcome = "abandoned"
            elif age(thread, ctx) > thread.get("max_turns", max_turns(cfg)):
                outcome = "expired"
            if outcome:
                effects.append(Effect("side.conclude", reason=outcome, id=thread["id"], outcome=outcome, turn=turn))
        return effects

    # --- prompt --------------------------------------------------------------------

    def prompt_sections(self, cfg, ctx):
        offer = current_offer(cfg, ctx)
        if not offer:
            return {}
        line = _line(offer, ctx)
        if not line:
            return {}
        return {"texture" if offer["kind"] == "texture" else "offer": "\n" + line}


# --- appliers ------------------------------------------------------------------------------

def _state(ctx):
    return ctx["state"].setdefault("mechanics", {}).setdefault("side_threads", {
        "active": [], "concluded": [], "last_started_turn": None, "next_id": 1, "offered": None})


def _apply_start(ctx, effect):
    st = _state(ctx)
    thread = dict(effect.payload["thread"])
    st.setdefault("active", []).append(thread)
    st["last_started_turn"] = thread["started_turn"]
    st["next_id"] = int(st.get("next_id") or 1) + 1
    if thread.get("origin") == "player" and "pursuit" in st:
        st["pursuit"] = None
    followed = thread.get("followed")
    if followed:
        for done in st.get("concluded", []):
            if done.get("id") == followed:
                done["followed_by"] = thread["id"]


def _apply_progress(ctx, effect):
    st = _state(ctx)
    for thread in st.get("active", []):
        if thread["id"] == effect.payload["id"]:
            thread["beat_index"] = effect.payload["beat_index"]
            thread["last_offered_turn"] = effect.payload["turn"]
            thread["unadvanced"] = effect.payload.get("unadvanced", 0)
            if effect.payload["detected"]:
                thread["detected"] = True


def _apply_pursuit(ctx, effect):
    _state(ctx)["pursuit"] = effect.payload["candidate"]


def _apply_wrap(ctx, effect):
    for thread in _state(ctx).get("active", []):
        if thread["id"] == effect.payload["id"]:
            thread["wrap_from"] = effect.payload["turn"]


def _apply_conclude(ctx, effect):
    st = _state(ctx)
    for thread in list(st.get("active", [])):
        if thread["id"] == effect.payload["id"]:
            st["active"].remove(thread)
            st.setdefault("concluded", []).append({
                "id": thread["id"], "recipe": thread.get("recipe"), "origin": thread.get("origin", "engine"),
                "title": thread["title"], "premise": thread.get("premise", ""), "cast": thread.get("cast", {}),
                "turns": [thread["started_turn"], effect.payload["turn"]], "outcome": effect.payload["outcome"]})


def _apply_vignette(ctx, effect):
    st = _state(ctx)
    st["last_vignette_turn"] = effect.payload["turn"]
    st["vignette_cursor"] = int(st.get("vignette_cursor") or 0) + 1
    memory = [s for s in st.get("vignette_subjects", []) if s != effect.payload["subject"]]
    memory.append(effect.payload["subject"])
    st["vignette_subjects"] = memory[-VIGNETTE_MEMORY:]


ENGINE = register(EpisodicThreads())
register_effect("side.start", _apply_start)
register_effect("side.progress", _apply_progress)
register_effect("side.wrap", _apply_wrap)
register_effect("side.pursuit", _apply_pursuit)
register_effect("side.conclude", _apply_conclude)
register_effect("side.vignette", _apply_vignette)
