"""mechanics.stats.readout.delta_block - the engine composes the readout, not the model.

`readout.token` already took the *absolute* figures away from the prompt (see
test_stat_readout.py). This takes the rest of the block: the model writes a marker and at
most a short clause, and the engine composes the wrapper and the priced deltas around it.

Two things fall out of that which a prompt rule could not guarantee:

  - "a status readout requires a reason" stops being a rule. No priced movement, no block,
    because there is nothing for the engine to compose.
  - a multi-clause tactical briefing becomes structurally impossible, because the only free
    prose left is one clause with a word cap.

Run directly: python3 test/test_stat_delta_block.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics

BASE = {
    "engine": "bounded_counter",
    "visible": True,
    "floor": 0,
    "ceiling": 100,
    "axes": {
        "sync": {"costs": {"consonance.woken": 7}},
        "frame": {"costs": {"hull.holed": -12, "hull.scraped": -5, "repair.patch": 4}},
        "trace": {"per_turn": -1, "costs": {"ran.loud": 6}},
    },
    "readout": {
        "token": "[[STATS]]",
        "labels": {"sync": "SYNC", "frame": "FRAME", "trace": "TRACE"},
        "entry_format": "**{label}** {value}",
        "separator": " - ",
    },
}
DELTA_BLOCK = {
    "marker": "READOUT",
    "wrapper": "**[ SYSTEM ]**\n*{body}*",
    "fact_format": "{label} {signed}.",
    "separator": " ",
    "clause_max_words": 12,
}


def build(delta_block=None, stats=None):
    """A ctx whose story carries BASE, optionally with a delta_block authored."""
    ctx = se.state_store.load_state("deltablocktest", se.state_store.DEFAULT_STORY_SLUG)
    story = se.state_store.thaw(ctx["story"])
    cfg = {**BASE, "readout": dict(BASE["readout"])}
    if delta_block is not None:
        cfg["readout"]["delta_block"] = delta_block
    story.setdefault("mechanics", {})["stats"] = cfg
    ctx["story"] = se.state_store.freeze(story)
    ctx["state"]["protagonist"]["stats"] = dict(stats or {"sync": 30, "frame": 29, "trace": 13})
    return ctx


def turn(ctx, *event_keys):
    """Run one engine turn with the given priced events named by the observation pass."""
    mechanics.run_turn_pipeline(ctx, [{"type": "stat_event", "key": k} for k in event_keys])


# --- P-2: no delta_block authored means the feature does not exist ---------------------
ctx = build()
turn(ctx, "hull.scraped")
text = "Prose.\n\n[[READOUT: That was avoidable.]]"
assert se.apply_stat_readouts(ctx, text) == text, \
    "a story with no delta_block must have its narration passed through untouched, marker and all"
print("OK: P-2 - no delta_block authored, narration untouched")

# --- a priced event composes the block; the model supplies only the clause -------------
ctx = build(DELTA_BLOCK)
turn(ctx, "hull.scraped")
out = se.apply_stat_readouts(ctx, "Prose.\n\n[[READOUT: That was avoidable.]]")
assert out == "Prose.\n\n**[ SYSTEM ]**\n*FRAME -5. That was avoidable.*", out
print("OK: priced event -> engine composes wrapper and figure, model supplies the clause")

# --- the same marker with no clause at all is legitimate ------------------------------
ctx = build(DELTA_BLOCK)
turn(ctx, "hull.scraped")
assert se.apply_stat_readouts(ctx, "Prose.\n\n[[READOUT]]") == \
    "Prose.\n\n**[ SYSTEM ]**\n*FRAME -5.*"
print("OK: a bare marker renders the figure alone")

# --- no priced movement -> no block. This is the cadence rule, made structural ---------
ctx = build(DELTA_BLOCK)
turn(ctx)  # drift only
assert ctx["state"]["protagonist"]["stats"]["trace"] == 12, "per_turn drift must still tick"
assert se.apply_stat_readouts(ctx, "Prose.\n\n[[READOUT: Something happened.]]") == "Prose.", \
    "drift alone must not qualify a readout, or every turn on a drifting story would render one"
print("OK: drift alone renders nothing - 'requires a reason' is now structural")

# --- an over-long clause is dropped, the figure still renders --------------------------
ctx = build(DELTA_BLOCK)
turn(ctx, "hull.scraped")
briefing = ("[[READOUT: The yard hand sits in full view of the only lit crossing and the "
            "crane's cost exceeds the Cape's liquidity.]]")
assert se.apply_stat_readouts(ctx, f"Prose.\n\n{briefing}") == \
    "Prose.\n\n**[ SYSTEM ]**\n*FRAME -5.*", "a clause over the cap must be dropped, not truncated"
print("OK: an over-cap clause is dropped whole; a tactical briefing cannot render")

# --- several events against one axis net out, and clamping is respected ---------------
ctx = build(DELTA_BLOCK)
turn(ctx, "hull.holed", "repair.patch")           # -12 then +4 against frame 29
assert ctx["state"]["protagonist"]["stats"]["frame"] == 21
assert se.apply_stat_readouts(ctx, "[[READOUT]]") == "**[ SYSTEM ]**\n*FRAME -8.*"
print("OK: several events against one axis report their net movement")

ctx = build(DELTA_BLOCK, stats={"sync": 30, "frame": 3, "trace": 13})
turn(ctx, "hull.holed")                            # charged -12 against frame 3, floor 0
assert ctx["state"]["protagonist"]["stats"]["frame"] == 0
assert se.apply_stat_readouts(ctx, "[[READOUT]]") == "**[ SYSTEM ]**\n*FRAME -3.*", \
    "a clamped move must report what it actually came to, never what it was charged"
print("OK: a clamped move reports the movement, not the charge")

# --- multi-axis blocks follow authored label order and survive the handwritten backstop -
ctx = build(DELTA_BLOCK)
turn(ctx, "consonance.woken", "ran.loud")          # sync +7, trace +6
out = se.apply_stat_readouts(ctx, "[[READOUT: Noted.]]")
assert out == "**[ SYSTEM ]**\n*SYNC +7. TRACE +6. Noted.*", out
print("OK: multi-axis block follows authored label order and survives the backstop regex")

# --- the absolute token still works alongside, and still renders absolutes -------------
ctx = build(DELTA_BLOCK)
turn(ctx, "hull.scraped")
out = se.apply_stat_readouts(ctx, "[[READOUT]]\n\n[[STATS]]")
assert out == "**[ SYSTEM ]**\n*FRAME -5.*\n\n**SYNC** 30 - **FRAME** 24 - **TRACE** 12", out
print("OK: delta block and absolute token coexist - deltas and absolutes stay distinct")

print("\nALL CHECKS PASSED: test_stat_delta_block")


# =====================================================================================
# Tiers: a figure sitting in an authored band, and the word cap that band imposes.
# Ported from scored_axis, which had the shape already - a threshold that changes what
# the narration may do, rather than one that only labels a number.
# =====================================================================================

TIERED = {
    **BASE,
    "axes": {
        **BASE["axes"],
        "quorum": {
            "costs": {"node.relit": 6, "part.cannibalised": -7},
            "tiers": [
                {"at": 0, "label": "fragmentary", "clause_max_words": 4,
                 "narration": "It answers in fragments. It cannot hold a sentence together yet."},
                {"at": 35, "label": "surfacing", "clause_max_words": 14,
                 "narration": "It has started saying I, and notices when it does."},
                {"at": 85, "label": "present", "clause_max_words": 30},
            ],
        },
    },
}
TIER_BLOCK = {**DELTA_BLOCK, "clause_max_words": 4, "clause_max_words_axis": "quorum"}


def tiered(stats):
    ctx = se.state_store.load_state("tiertest", se.state_store.DEFAULT_STORY_SLUG)
    story = se.state_store.thaw(ctx["story"])
    cfg = {**TIERED, "readout": dict(BASE["readout"])}
    cfg["readout"]["labels"] = {**BASE["readout"]["labels"], "quorum": "QUORUM"}
    cfg["readout"]["delta_block"] = TIER_BLOCK
    story.setdefault("mechanics", {})["stats"] = cfg
    ctx["story"] = se.state_store.freeze(story)
    ctx["state"]["protagonist"]["stats"] = dict(stats)
    return ctx


engine = mechanics.bound_for(tiered({"quorum": 0})["story"], "stats").engine
cfg = se.state_store.thaw(tiered({"quorum": 0})["story"])["mechanics"]["stats"]

# --- tier_for scans upward and takes the highest threshold actually reached -----------
assert engine.tier_for(cfg, "quorum", 0)["label"] == "fragmentary"
assert engine.tier_for(cfg, "quorum", 34)["label"] == "fragmentary"
assert engine.tier_for(cfg, "quorum", 35)["label"] == "surfacing"
assert engine.tier_for(cfg, "quorum", 84)["label"] == "surfacing"
assert engine.tier_for(cfg, "quorum", 100)["label"] == "present"
assert engine.tier_for(cfg, "frame", 29) is None, "an axis authoring no tiers has none"
print("OK: tier_for takes the highest threshold reached, and is None for an untiered axis")

# --- the cap is the tier's, and it is enforced, so terseness is measured not requested -
ctx = tiered({"sync": 30, "frame": 29, "trace": 13, "quorum": 0})
turn(ctx, "node.relit")
out = se.apply_stat_readouts(ctx, "[[READOUT: It cannot hold a sentence together yet at all.]]")
assert out == "**[ SYSTEM ]**\n*QUORUM +6.*", out
print("OK: at the lowest tier a 9-word clause is dropped - 4 words is all it may spend")

ctx = tiered({"sync": 30, "frame": 29, "trace": 13, "quorum": 90})
turn(ctx, "node.relit")
out = se.apply_stat_readouts(ctx, "[[READOUT: It cannot hold a sentence together yet at all.]]")
assert out == "**[ SYSTEM ]**\n*QUORUM +6. It cannot hold a sentence together yet at all.*", out
print("OK: the same clause survives at the top tier - the cap rose with the figure")

# --- the tier's authored line reaches the prompt, and only while that tier is current --
low = tiered({"sync": 30, "frame": 29, "trace": 13, "quorum": 10})
high = tiered({"sync": 30, "frame": 29, "trace": 13, "quorum": 40})
low_p, high_p = se.build_system_prompt(low), se.build_system_prompt(high)
assert "cannot hold a sentence together" in low_p and "started saying I" not in low_p
assert "started saying I" in high_p and "cannot hold a sentence together" not in high_p
print("OK: only the currently-occupied tier's line reaches the prompt, never both")

# --- a tier with a label but no narration spends no prompt on itself ------------------
top = se.build_system_prompt(tiered({"sync": 30, "frame": 29, "trace": 13, "quorum": 90}))
assert "(present)" not in top, "a label-only tier must not render a line of its own"
print("OK: a label-only tier contributes no prompt text")

print("\nALL CHECKS PASSED: test_stat_delta_block (tiers)")
