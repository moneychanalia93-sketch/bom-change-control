"""In-memory BOM model: parts, BOM lines, multi-level explosion and where-used.

A BOM (bill of materials) is a list of "parent contains N of child" lines.
Everything here works on plain Python objects so it can be tested without a
database. `db.load_bom` builds a `Bom` from SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass

STATUSES = ("Draft", "Released", "Obsolete")


@dataclass(frozen=True)
class Part:
    """One part or assembly (an assembly is simply a part that has children)."""

    part_number: str
    description: str
    revision: str
    status: str
    uom: str
    owner: str


@dataclass(frozen=True)
class BomLine:
    """`parent` contains `quantity` x `child`, measured in `unit`."""

    parent: str
    child: str
    quantity: float
    unit: str
    child_rev: str = ""


@dataclass(frozen=True)
class ExplodedLine:
    """Total quantity of one leaf part needed for one unit of an assembly."""

    part_number: str
    unit: str
    quantity: float


@dataclass(frozen=True)
class Affected:
    """An assembly that uses a part directly (level 1) or through others (level 2+)."""

    part_number: str
    level: int
    path: tuple[str, ...]  # from the affected assembly down to the starting part


class CycleError(Exception):
    """A part (directly or indirectly) contains itself."""

    def __init__(self, path: list[str]) -> None:
        self.path = path
        super().__init__("circular structure: " + " -> ".join(path))


def fmt_qty(quantity: float) -> str:
    """Format 2.0 as '2' and 0.5 as '0.5'."""
    return f"{quantity:g}"


def bump_revision(revision: str) -> str:
    """Return the next revision: A->B, Z->AA, AZ->BA, 1->2."""
    if revision.isdigit():
        return str(int(revision) + 1)
    if revision.isalpha() and revision.isupper():
        chars = list(revision)
        for i in range(len(chars) - 1, -1, -1):
            if chars[i] != "Z":
                chars[i] = chr(ord(chars[i]) + 1)
                return "".join(chars)
            chars[i] = "A"
        return "A" + "".join(chars)
    raise ValueError(f"cannot bump revision {revision!r}: use letters (A, B, ...) or digits")


class Bom:
    """A set of parts plus the BOM lines that connect them."""

    def __init__(self, parts: list[Part], lines: list[BomLine]) -> None:
        self.parts = list(parts)
        self.lines = list(lines)
        self._by_number: dict[str, Part] = {}
        for part in self.parts:  # if a part number is duplicated, the first row wins
            self._by_number.setdefault(part.part_number, part)
        self.children: dict[str, list[BomLine]] = {}
        self.parents: dict[str, list[BomLine]] = {}
        for line in self.lines:
            self.children.setdefault(line.parent, []).append(line)
            self.parents.setdefault(line.child, []).append(line)

    def part(self, part_number: str) -> Part | None:
        return self._by_number.get(part_number)

    def line(self, parent: str, child: str) -> BomLine | None:
        for line in self.children.get(parent, []):
            if line.child == child:
                return line
        return None

    def descendants(self, part_number: str) -> set[str]:
        """Every part found anywhere below `part_number` (cycle-safe)."""
        seen: set[str] = set()
        pending = [part_number]
        while pending:
            for line in self.children.get(pending.pop(), []):
                if line.child not in seen:
                    seen.add(line.child)
                    pending.append(line.child)
        return seen

    def explode(self, root: str, multiplier: float = 1.0) -> list[ExplodedLine]:
        """Flatten `root` to total quantity per leaf part (and unit).

        Raises CycleError if the structure loops, ValueError if `root` is unknown.
        shortcut: no memoisation, so a very wide shared structure is re-walked
        per path; fine for BOMs of this size, add a cache if one gets huge.
        """
        if root not in self._by_number and root not in self.children:
            raise ValueError(f"unknown part {root}")
        totals: dict[tuple[str, str], float] = {}

        def walk(part_number: str, factor: float, stack: list[str]) -> None:
            if part_number in stack:
                raise CycleError(stack[stack.index(part_number):] + [part_number])
            for line in self.children.get(part_number, []):
                qty = factor * line.quantity
                if line.child in self.children:
                    walk(line.child, qty, stack + [part_number])
                else:  # a leaf: nothing below it
                    key = (line.child, line.unit)
                    totals[key] = totals.get(key, 0.0) + qty

        walk(root, multiplier, [])
        return [ExplodedLine(pn, unit, qty) for (pn, unit), qty in sorted(totals.items())]

    def where_used(self, part_number: str) -> list[Affected]:
        """Every assembly above `part_number`, at every level, nearest first."""
        found: list[Affected] = []
        seen = {part_number}
        frontier = [(part_number, (part_number,))]
        level = 0
        while frontier:
            level += 1
            next_frontier: list[tuple[str, tuple[str, ...]]] = []
            for current, path in frontier:
                for line in self.parents.get(current, []):
                    if line.parent in seen:
                        continue
                    seen.add(line.parent)
                    new_path = (line.parent,) + path
                    found.append(Affected(line.parent, level, new_path))
                    next_frontier.append((line.parent, new_path))
            frontier = next_frontier
        return sorted(found, key=lambda a: (a.level, a.part_number))

    def tree_lines(self, root: str) -> list[str]:
        """Indented text tree of `root` for display. Stops at a repeated part."""
        out: list[str] = []

        def label(part_number: str) -> str:
            part = self.part(part_number)
            if part is None:
                return f"{part_number}  (unknown part)"
            return f"{part_number}  {part.description} [rev {part.revision}, {part.status}]"

        def walk(part_number: str, depth: int, stack: list[str]) -> None:
            for line in self.children.get(part_number, []):
                head = f"{'  ' * depth}{fmt_qty(line.quantity)} {line.unit} x "
                if line.child in stack:
                    out.append(head + f"{line.child}  (circular, not expanded)")
                    continue
                out.append(head + label(line.child))
                walk(line.child, depth + 1, stack + [line.child])

        out.append(label(root))
        walk(root, 1, [root])
        return out
