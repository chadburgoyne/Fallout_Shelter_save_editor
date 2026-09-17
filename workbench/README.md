# workbench

Our own tooling, kept separate from the two editors we are diagnosing.

## Setup

```
pip install pycryptodome
```

## Auditing a save

```
cd workbench
python3 audit_save.py /path/to/Vault1.sav
```

Exits non-zero when it finds something. Run it on a save before an edit and
again after, and the diff in findings is what the editor did.

## Save format

`Vault*.sav` is base64 text wrapping AES-256-CBC ciphertext:

| | |
|---|---|
| key | PBKDF2-HMAC-SHA1, password `UGxheWVy`, salt `tu89geji340t89u2`, 1000 iterations, 32 bytes |
| IV | `tu89geji340t89u2` (fixed, not random per save) |
| padding | PKCS#7 |
| plaintext | compact UTF-8 JSON, no whitespace between tokens |

Both halves of that are verified byte-exact against a real save: decrypting and
re-encrypting an untouched file reproduces it exactly, as does parsing the JSON
and re-serializing it with `separators=(",", ":")`.

## What gets checked

- **Round-trip fidelity** — whether the file survives decrypt/encrypt and
  JSON parse/serialize untouched. An editor that fails this is losing data
  before it applies a single edit.
- **Duplicate ids** across dwellers, rooms and Mr Handys.
- **Stale id counters** — `dwellers.id` and `dwellers.mrhId` are next-id
  allocators. If a counter sits at or below an id already in use, the next
  dweller the game creates collides with an existing one.
- **Cross references** — `dweller.savedRoom` and `room.dwellers` are two
  records of the same fact and have to agree in both directions. Dangling
  refs, one-way links, ghost occupants and double-booked dwellers all break
  the vault.
- **Orphaned dwellers** — a living dweller must be in a room, a wasteland
  team, or the quest roster. One that is in none of those has no position for
  the game to place it at.
- **Capacity** — two dwellers per merged room segment.
- **Grid overlap** — two rooms in the same cell.

## Layout notes

Rooms are placed by `row` and `col`, spanning `mergeLevel` columns. Dead
dwellers are tracked in `room.deadDwellers`, separately from `room.dwellers`,
so a corpse is not an orphan. Dwellers away from the vault carry
`savedRoom: -1` and appear in `vault.wasteland.teams` or `questDwellers`.
