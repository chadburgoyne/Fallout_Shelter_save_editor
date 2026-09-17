"""Consistency checks for a decrypted Fallout Shelter save.

Everything here is read-only. Each check appends human-readable findings so a
save can be inspected before and after an edit to see what an editor changed.
"""
import collections


def _rooms(save):
    return save.get("vault", {}).get("rooms", [])


def _dwellers(save):
    return save.get("dwellers", {}).get("dwellers", [])


def locate_dwellers(save):
    """Map every dweller id to the places the save claims it is.

    A living dweller must be in exactly one of: a room's occupant list, a
    wasteland team, or the quest roster. Anything else has no position for the
    game to render it at.
    """
    in_room, dead_in_room = {}, {}
    for room in _rooms(save):
        for did in room.get("dwellers", []):
            in_room.setdefault(did, []).append(room["deserializeID"])
        for did in room.get("deadDwellers", []):
            dead_in_room.setdefault(did, []).append(room["deserializeID"])

    in_team = {}
    for team in save.get("vault", {}).get("wasteland", {}).get("teams", []):
        for did in team.get("dwellers", []):
            in_team.setdefault(did, []).append(team.get("teamIndex"))

    in_quest = {x["serializeId"] for x in save.get("questDwellers", {}).get("dwellers", [])}

    located = {}
    for dweller in _dwellers(save):
        did = dweller["serializeId"]
        where = []
        if did in in_room:
            where.append("room:" + ",".join(str(r) for r in in_room[did]))
        if did in in_team:
            where.append("wasteland-team:" + ",".join(str(t) for t in in_team[did]))
        if did in in_quest:
            where.append("quest")
        if did in dead_in_room:
            where.append("dead-in-room:" + ",".join(str(r) for r in dead_in_room[did]))
        located[did] = where
    return located


def check_orphans(save, out):
    """Living dwellers the game has nowhere to place. Crashes the vault on load."""
    located = locate_dwellers(save)
    orphans = []
    for dweller in _dwellers(save):
        did = dweller["serializeId"]
        if located[did]:
            continue
        if dweller.get("health", {}).get("permaDeath") or dweller.get("deathTime", -1) != -1:
            continue  # dead and already accounted for elsewhere
        orphans.append((did, f'{dweller["name"]} {dweller["lastName"]}'.strip()))
    if orphans:
        out.append(f"ORPHANED DWELLERS: {len(orphans)} living dwellers have no location")
        for did, name in orphans:
            out.append(f"    id {did}: {name} (savedRoom=-1, in no room/team/quest)")
    return orphans


def check_id_allocator(save, out):
    """Next-id counters must sit above every id already in use."""
    problems = []
    ids = [x["serializeId"] for x in _dwellers(save)]
    if ids:
        counter, highest = save["dwellers"].get("id"), max(ids)
        if counter is not None and counter <= highest:
            collide = sorted(i for i in ids if i >= counter)
            out.append(
                f"STALE DWELLER ID COUNTER: dwellers.id={counter} but ids run up to {highest}"
            )
            out.append(f"    {len(collide)} existing ids are at or above the counter: {collide}")
            out.append(f"    the next dweller created will reuse id {counter}")
            problems.append(("dwellers.id", counter, highest))

    actors = [a["serializeId"] for a in save.get("dwellers", {}).get("actors", [])]
    if actors:
        mrh, highest = save["dwellers"].get("mrhId"), max(actors)
        if mrh is not None and mrh <= highest:
            out.append(f"STALE MR HANDY ID COUNTER: mrhId={mrh} but actor ids run up to {highest}")
            problems.append(("dwellers.mrhId", mrh, highest))
    return problems


def check_cross_references(save, out):
    """Room occupant lists and dweller room assignments must agree both ways."""
    problems = []
    rooms_by_id = {r["deserializeID"]: r for r in _rooms(save)}
    dwellers_by_id = {x["serializeId"]: x for x in _dwellers(save)}

    for dweller in _dwellers(save):
        room_id = dweller.get("savedRoom", -1)
        if room_id == -1:
            continue
        room = rooms_by_id.get(room_id)
        if room is None:
            out.append(f"DANGLING ROOM REF: dweller {dweller['serializeId']} -> room {room_id} does not exist")
            problems.append(dweller["serializeId"])
        elif dweller["serializeId"] not in room.get("dwellers", []):
            out.append(f"ONE-WAY LINK: dweller {dweller['serializeId']} claims room {room_id}, room does not list them")
            problems.append(dweller["serializeId"])

    seen = collections.Counter()
    for room in _rooms(save):
        for did in room.get("dwellers", []):
            seen[did] += 1
            dweller = dwellers_by_id.get(did)
            if dweller is None:
                out.append(f"GHOST OCCUPANT: room {room['deserializeID']} lists dweller {did}, who does not exist")
                problems.append(did)
            elif dweller.get("savedRoom") != room["deserializeID"]:
                out.append(
                    f"ONE-WAY LINK: room {room['deserializeID']} lists dweller {did}, "
                    f"but savedRoom={dweller.get('savedRoom')}"
                )
                problems.append(did)
    for did, count in seen.items():
        if count > 1:
            out.append(f"DOUBLE-BOOKED: dweller {did} is listed in {count} rooms")
            problems.append(did)
    return problems


def check_duplicate_ids(save, out):
    problems = []
    for label, items, key in (
        ("dweller", _dwellers(save), "serializeId"),
        ("room", _rooms(save), "deserializeID"),
        ("actor", save.get("dwellers", {}).get("actors", []), "serializeId"),
    ):
        counts = collections.Counter(i[key] for i in items)
        for value, count in counts.items():
            if count > 1:
                out.append(f"DUPLICATE {label.upper()} ID: {value} appears {count} times")
                problems.append((label, value))
    return problems


def check_capacity(save, out):
    """A room holds two dwellers per merged segment."""
    problems = []
    for room in _rooms(save):
        capacity = room.get("mergeLevel", 1) * 2
        occupants = len(room.get("dwellers", []))
        if occupants > capacity:
            out.append(
                f"OVER CAPACITY: room {room['deserializeID']} ({room['type']}) "
                f"holds {occupants}, capacity {capacity}"
            )
            problems.append(room["deserializeID"])
    return problems


def check_grid(save, out):
    """Two rooms may not occupy the same grid cell."""
    problems = []
    occupied = collections.defaultdict(list)
    for room in _rooms(save):
        for col in range(room["col"], room["col"] + room.get("mergeLevel", 1)):
            occupied[(room["row"], col)].append((room["deserializeID"], room["type"]))
    for (row, col), here in occupied.items():
        if len(here) > 1:
            out.append(f"OVERLAPPING ROOMS at row {row} col {col}: {here}")
            problems.append((row, col))
    return problems


def run(save):
    """Run every check. Returns (findings, ok)."""
    out = []
    found = any([
        bool(check_duplicate_ids(save, out)),
        bool(check_id_allocator(save, out)),
        bool(check_cross_references(save, out)),
        bool(check_orphans(save, out)),
        bool(check_capacity(save, out)),
        bool(check_grid(save, out)),
    ])
    return out, not found
