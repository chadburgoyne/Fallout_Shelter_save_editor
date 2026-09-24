"""Read and write Vault*.sav without disturbing anything we did not edit."""
import json

from .crypto import decrypt, encrypt

# The game emits compact JSON with no whitespace between tokens. Re-serializing
# with Python's defaults inserts spaces and changes the byte stream for parts of
# the save that were never edited.
SEPARATORS = (",", ":")


def load(path):
    with open(path, "rb") as handle:
        return json.loads(decrypt(handle.read()))


def dumps(save):
    return json.dumps(save, separators=SEPARATORS, ensure_ascii=False).encode("utf-8")


def save_to(save, path):
    with open(path, "wb") as handle:
        handle.write(encrypt(dumps(save)))


def fidelity(path):
    """Decode and re-encode an untouched save; report where the bytes diverge.

    A save that does not survive this has already lost data before any edit is
    applied, which is the failure mode worth ruling out first in any editor.
    """
    with open(path, "rb") as handle:
        original = handle.read()
    plain = decrypt(original)
    reencrypted = encrypt(plain)
    roundtrip = dumps(json.loads(plain))

    return {
        "crypto_exact": reencrypted == original,
        "json_exact": roundtrip == plain,
        "plain_bytes": len(plain),
        "json_bytes": len(roundtrip),
        "first_diff": _first_diff(plain, roundtrip),
    }


def _first_diff(a, b):
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            lo = max(0, i - 40)
            return {
                "offset": i,
                "original": a[lo:i + 40].decode("utf-8", "replace"),
                "roundtrip": b[lo:i + 40].decode("utf-8", "replace"),
            }
    return None if len(a) == len(b) else {"offset": min(len(a), len(b)), "note": "length differs"}
