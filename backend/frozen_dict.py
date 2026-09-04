"""FrozenDict: the enforcement mechanism behind schema v2's story/state split (see
SCHEMA_V2_SPEC.md §2.2.1). Authored content (ctx["story"]) is supposed to be immutable at
runtime - the save holds only what changed. A convention alone doesn't stop a bug from
writing into the authored half by accident, so state_store.load_state() wraps every dict
under ctx["story"] in this recursively, turning "authored content is immutable" into a
raised TypeError instead of a silent, hard-to-spot mutation.

Deliberately NOT done via __getattr__ attribute access or schema-generated dataclasses -
see SCHEMA_V2_SPEC.md §2.2.1 for why those were rejected. A plain dict subclass means
json.load/json.dump work on it unchanged (a FrozenDict serializes exactly like the plain
dict it wraps), and every existing dict-access call site needs no restructuring beyond
which half of ctx it starts from.
"""


class FrozenDict(dict):
    """A dict that raises TypeError on any mutating call. Wraps every nested dict/list
    recursively (see freeze()) so reaching several levels into ctx["story"] and mutating
    there is caught too, not just a top-level assignment."""

    _MUTATORS = (
        "__setitem__", "__delitem__", "update", "pop", "setdefault", "clear",
        "popitem",
    )

    def _raise(self, *a, **k):
        raise TypeError(
            "ctx['story'] is authored content and immutable at runtime - write into "
            "ctx['state'] instead (see SCHEMA_V2_SPEC.md §2.2)"
        )

    for _name in _MUTATORS:
        locals()[_name] = _raise
    del _name


class FrozenList(list):
    """The list equivalent of FrozenDict - a story's acts/rules/style bullets/etc. are
    lists, and those need the same write-protection as their enclosing dicts."""

    _MUTATORS = (
        "__setitem__", "__delitem__", "append", "extend", "insert", "remove", "pop",
        "clear", "sort", "reverse", "__iadd__",
    )

    def _raise(self, *a, **k):
        raise TypeError(
            "ctx['story'] is authored content and immutable at runtime - write into "
            "ctx['state'] instead (see SCHEMA_V2_SPEC.md §2.2)"
        )

    for _name in _MUTATORS:
        locals()[_name] = _raise
    del _name


def freeze(value):
    """Recursively wraps a plain (json.load-produced) structure of dicts/lists/scalars in
    FrozenDict/FrozenList. Applied once, right after a template loads - see
    state_store.load_state()."""
    if isinstance(value, dict):
        return FrozenDict((k, freeze(v)) for k, v in value.items())
    if isinstance(value, list):
        return FrozenList(freeze(v) for v in value)
    return value


def thaw(value):
    """Inverse of freeze(): a plain, deep, mutable copy - used when a caller genuinely
    needs to derive a fresh mutable structure from authored content (e.g. seeding a new
    save's runtime state from a template default)."""
    if isinstance(value, dict):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, list):
        return [thaw(v) for v in value]
    return value


def assert_unmutated(original_json: dict, ctx_story: dict, context: str = ""):
    """Belt-and-braces check independent of FrozenDict raising at the point of mutation
    (SCHEMA_V2_SPEC.md §2.2.1) - catches any path that swapped in a fresh dict/list
    somewhere under ctx["story"] rather than writing through the wrapper (which wouldn't
    raise, since a fresh object isn't frozen). Compares against a plain-dict snapshot taken
    at load time. Raises AssertionError, not silently logs - an authored-content mutation
    that slipped past FrozenDict is a bug worth failing loudly on."""
    current = thaw(ctx_story)
    if current != original_json:
        raise AssertionError(
            f"ctx['story'] was mutated during the request{': ' + context if context else ''} - "
            "authored content must never change at runtime (SCHEMA_V2_SPEC.md §2.2.1)"
        )
