import csv
import io
import json

from players import username_key


def read_inbox(path):
    if not path.exists():
        return []
    entries = []
    with path.open(newline="", encoding="utf-8-sig") as file:
        for line, row in enumerate(csv.reader(file, strict=True), start=1):
            names = tuple(name.replace("\xa0", " ").strip() for name in row)
            if not names or names == ("",):
                continue
            if len(names) != 2 or any(name and not username_key(name) for name in names):
                raise ValueError(f"{path}:{line}: expected old,new; either name may be empty")
            if any(names):
                entries.append(names)
    return entries


def resolve_renames(entries):
    direct = {}
    for entry in entries:
        old, new = entry
        if not old:
            continue
        key = username_key(old)
        if key in direct and username_key(direct[key]) != username_key(new):
            raise ValueError(f"Conflicting name changes for {old!r}")
        direct.setdefault(key, new)

    resolved = {}
    for start in direct:
        path = set()
        key = start
        name = direct[start]
        while key in direct and key not in resolved:
            if key in path:
                raise ValueError(f"Cyclic name changes involving {start!r}")
            path.add(key)
            name = direct[key]
            target = username_key(name)
            if target == key:
                break
            key = target
        name = resolved.get(key, name)
        for source in path:
            resolved[source] = name

    # Apply the requested spelling to existing destination entries too.
    targets = {username_key(name): name for name in resolved.values() if name}
    return {key: targets[username_key(name)] if name else "" for key, name in resolved.items()} | targets


def rename_scores(scores, renames):
    renamed = {}
    for old in scores.values():
        row = dict(old)
        row["RSN"] = renames.get(username_key(row["RSN"]), row["RSN"])
        if not row["RSN"]:
            continue
        key = username_key(row["RSN"])
        if key in renamed:
            previous = renamed[key]
            if (int(previous["TIME"]), int(previous["TOTEXP"])) > (int(row["TIME"]), int(row["TOTEXP"])):
                row, previous = previous, row
            for field in ("QPOINTS", "RSCORE"):
                if row.get(field, "") == "":
                    row[field] = previous.get(field, "")
        renamed[key] = row
    return renamed


def prepare_related_changes(data_dir, renames):
    changes = {}
    if not renames:
        return changes
    for filename in ("ironman-badges.csv", "removed-usernames.csv"):
        path = data_dir / filename
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            columns = reader.fieldnames
            original = list(reader)
        rows = []
        for row in original:
            name = renames.get(username_key(row["RSN"]), row["RSN"])
            if name:
                rows.append(dict(row, RSN=name))
        if filename == "ironman-badges.csv":
            unique = {}
            for row in rows:
                unique.setdefault(username_key(row["RSN"]), row)
            rows = list(unique.values())
        if rows != original:
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
            changes[path] = output.getvalue()

    state_path = data_dir / "username-maintenance.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        missing = state.get("missing", {})
        remaining = {key: value for key, value in missing.items() if username_key(key) not in renames}
        if missing != remaining:
            state["missing"] = remaining
            changes[state_path] = json.dumps(state, indent=2, sort_keys=True) + "\n"
    return changes


def write_related_changes(changes):
    for path, contents in changes.items():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(contents, encoding="utf-8", newline="")
        temporary.replace(path)


def consume_inbox(path, entries):
    consumed = {tuple(username_key(name) for name in entry) for entry in entries}
    remaining = [entry for entry in read_inbox(path)
                 if tuple(username_key(name) for name in entry) not in consumed]
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        csv.writer(file, lineterminator="\n").writerows(remaining)
    temporary.replace(path)
