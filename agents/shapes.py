"""Answer shapes from the eval's `answer:` field, shared by the harness and the grader.

A shape is the parsed YAML flow mapping, e.g. ``{records: int, usd: number}``,
``{ranked: [{product: str, usd: number}], k: 10}``, ``{table: [{program_year: int, usd: number}]}``
or ``{set: [str]}``. Top-level ``k`` is not an answer key: it is the length of ``ranked``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCALARS = ("int", "number", "str")


@dataclass(frozen=True)
class Field:
    key: str
    kind: str  # int | number | str | set | ranked | table
    item: tuple[tuple[str, str], ...] = ()  # ranked/table: (column, scalar type); set: (("", t),)
    k: int | None = None

    @property
    def is_list(self) -> bool:
        return self.kind in ("set", "ranked", "table")

    @property
    def key_columns(self) -> tuple[str, ...]:
        """Table rows are matched on their str columns, or on the first column if none is str."""
        strs = tuple(c for c, t in self.item if t == "str")
        return strs or (self.item[0][0],)

    @property
    def name_column(self) -> str:
        """Ranked items are identified by their first str column."""
        return next((c for c, t in self.item if t == "str"), self.item[0][0])


def parse_shape(shape: dict[str, Any]) -> list[Field]:
    if not isinstance(shape, dict) or not shape:
        raise ValueError(f"answer shape must be a non-empty mapping, got {shape!r}")
    k = shape.get("k")
    fields: list[Field] = []
    for key, spec in shape.items():
        if key == "k" and isinstance(spec, int):
            continue
        if isinstance(spec, str) and spec in SCALARS:
            fields.append(Field(key, spec))
        elif isinstance(spec, list) and len(spec) == 1 and isinstance(spec[0], dict):
            item = tuple((str(c), str(t)) for c, t in spec[0].items())
            if not item or any(t not in SCALARS for _, t in item):
                raise ValueError(f"bad item spec for {key!r}: {spec!r}")
            if key == "ranked":
                if not isinstance(k, int) or k < 1:
                    raise ValueError("a ranked shape needs an integer k")
                fields.append(Field(key, "ranked", item, k))
            else:
                fields.append(Field(key, "table", item))
        elif isinstance(spec, list) and len(spec) == 1 and spec[0] in SCALARS:
            fields.append(Field(key, "set", (("", spec[0]),)))
        else:
            raise ValueError(f"unsupported answer spec for {key!r}: {spec!r}")
    return fields


_JSON_TYPE = {"int": "integer", "number": "number", "str": "string"}


def _scalar_schema(name: str, typ: str) -> dict[str, Any]:
    s: dict[str, Any] = {"type": _JSON_TYPE[typ]}
    if typ != "str" and "percent" in name:
        s["description"] = "Percent in 0-100 units: 12.3 means 12.3%, not 0.123."
    elif typ == "number":
        s["description"] = "Plain number, no units or separators. Dollars are US dollars."
    elif typ == "str":
        s["description"] = "Exactly as the value appears in the data."
    return s


def answer_schema(fields: list[Field]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for f in fields:
        if f.kind in SCALARS:
            props[f.key] = _scalar_schema(f.key, f.kind)
            continue
        if f.kind == "set":
            props[f.key] = {
                "type": "array",
                "items": _scalar_schema(f.key, f.item[0][1]),
                "description": "Every member of the set, each once, in any order.",
            }
            continue
        item = {
            "type": "object",
            "properties": {c: _scalar_schema(c, t) for c, t in f.item},
            "required": [c for c, _ in f.item],
            "additionalProperties": False,
        }
        desc = (
            f"Exactly {f.k} items, best first."
            if f.kind == "ranked"
            else "One object per row, every row the question asks for."
        )
        props[f.key] = {"type": "array", "items": item, "description": desc}
    return {
        "type": "object",
        "properties": props,
        "required": [f.key for f in fields],
        "additionalProperties": False,
    }


def _type_ok(value: Any, typ: str) -> bool:
    if typ == "str":
        return isinstance(value, str)
    if isinstance(value, bool):
        return False
    if typ == "int":
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    return isinstance(value, int | float)


def validate_answer(answer: Any, fields: list[Field]) -> list[str]:
    """Problems that make an answer unusable (wrong keys or types), not whether it's right."""
    if not isinstance(answer, dict):
        return ["answer must be an object"]
    problems = []
    for f in fields:
        if f.key not in answer:
            problems.append(f"missing key {f.key!r}")
            continue
        v = answer[f.key]
        if f.kind in SCALARS:
            if not _type_ok(v, f.kind):
                problems.append(f"{f.key!r} must be {f.kind}, got {v!r}")
        elif not isinstance(v, list):
            problems.append(f"{f.key!r} must be a list")
        elif f.kind == "set":
            if not all(_type_ok(x, f.item[0][1]) for x in v):
                problems.append(f"{f.key!r} items must be {f.item[0][1]}")
        else:
            for i, row in enumerate(v):
                if not isinstance(row, dict):
                    problems.append(f"{f.key}[{i}] must be an object")
                    continue
                for c, t in f.item:
                    if c not in row or not _type_ok(row[c], t):
                        problems.append(f"{f.key}[{i}].{c} must be {t}")
            if f.kind == "ranked" and len(v) != f.k:
                problems.append(f"{f.key!r} must have exactly {f.k} items, got {len(v)}")
    extra = set(answer) - {f.key for f in fields}
    if extra:
        problems.append(f"unexpected keys {sorted(extra)}")
    return problems
