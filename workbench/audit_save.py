#!/usr/bin/env python3
"""Report everything structurally wrong with a Fallout Shelter save.

    python3 audit_save.py /path/to/Vault1.sav
"""
import sys

from fsedit import audit, saveio


def main(path):
    checks = saveio.fidelity(path)
    print(f"=== {path} ===")
    print(f"decrypt/encrypt round-trip exact : {checks['crypto_exact']}")
    print(f"JSON re-serialization exact      : {checks['json_exact']}")
    if not checks["json_exact"] and checks["first_diff"]:
        diff = checks["first_diff"]
        print(f"  first divergence at byte {diff['offset']}")
        for key in ("original", "roundtrip", "note"):
            if key in diff:
                print(f"    {key}: {diff[key]}")

    save = saveio.load(path)
    dwellers = save["dwellers"]["dwellers"]
    rooms = save["vault"]["rooms"]
    print(f"\nvault {save['vault']['VaultName']} | season {save.get('seasonId')} | app {save.get('appVersion','').strip()}")
    print(f"{len(dwellers)} dwellers, {len(rooms)} rooms, {len(save['dwellers'].get('actors', []))} Mr Handys")

    findings, ok = audit.run(save)
    print()
    if ok:
        print("No structural problems found.")
    else:
        print(f"{len(findings)} findings:")
        for line in findings:
            print(("  " if line.startswith("    ") else "  ") + line)
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
