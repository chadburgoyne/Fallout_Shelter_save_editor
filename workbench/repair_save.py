#!/usr/bin/env python3
"""Repair the id counters an editor leaves behind, without touching anything else.

    python3 repair_save.py Vault4.sav                 # report what it would change
    python3 repair_save.py Vault4.sav --write out.sav # write the repaired save

Only the two counters that behave as allocators are moved, and only ever
forward. Everything else in the save is passed through untouched, so a save
that needs no repair comes back byte-identical.
"""
import argparse
import sys

from fsedit import audit, saveio


def plan(save):
    """Work out which counters have fallen behind the ids in use."""
    changes = []
    rooms = [r["deserializeID"] for r in save.get("vault", {}).get("rooms", [])]
    actors = [a["serializeId"] for a in save.get("dwellers", {}).get("actors", [])]

    if rooms:
        current = save["constructMgr"]["roomDeserializeID"]
        if current < max(rooms):
            changes.append(("constructMgr.roomDeserializeID", current, max(rooms)))
    if actors:
        current = save["dwellers"]["mrhId"]
        if current < max(actors):
            changes.append(("dwellers.mrhId", current, max(actors)))
    return changes


def apply(save, changes):
    for path, _, new in changes:
        if path == "constructMgr.roomDeserializeID":
            save["constructMgr"]["roomDeserializeID"] = new
        elif path == "dwellers.mrhId":
            save["dwellers"]["mrhId"] = new


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("save")
    parser.add_argument("--write", metavar="OUT", help="write the repaired save here")
    args = parser.parse_args()

    save = saveio.load(args.save)
    changes = plan(save)

    if not changes:
        print("Nothing to repair: every id counter is at or above the ids in use.")
        return 0

    print("Counters to move forward:")
    for path, old, new in changes:
        print(f"  {path}: {old} -> {new}")

    if not args.write:
        print("\nRun again with --write OUT.sav to apply.")
        return 0

    apply(save, changes)
    remaining, ok = audit.run(save)
    saveio.save_to(save, args.write)
    print(f"\nWrote {args.write}")
    if ok:
        print("Audit of the repaired save is clean.")
    else:
        print("Audit still reports:")
        for line in remaining:
            print(f"  {line}")
    print("\nBack up your original before putting this in place, and if you are on"
          "\nSteam, turn off Cloud for the game first so it cannot overwrite the file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
