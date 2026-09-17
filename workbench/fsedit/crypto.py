"""Fallout Shelter save encryption.

Verified byte-exact: decrypt(encrypt(x)) == x and encrypt(decrypt(save)) == save
for an untouched Vault*.sav.
"""
import base64
import hashlib

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

IV = b"tu89geji340t89u2"
SALT = b"tu89geji340t89u2"
PASSWORD = b"UGxheWVy"
KEY = hashlib.pbkdf2_hmac("sha1", PASSWORD, SALT, 1000, 32)


def decrypt(b64text):
    """Encrypted .sav bytes -> raw JSON bytes."""
    return unpad(AES.new(KEY, AES.MODE_CBC, IV).decrypt(base64.b64decode(b64text)), 16)


def encrypt(plain):
    """Raw JSON bytes -> encrypted .sav bytes."""
    return base64.b64encode(AES.new(KEY, AES.MODE_CBC, IV).encrypt(pad(plain, 16)))
