"""密码哈希与校验（标准库 pbkdf2_sha256）。"""
import hashlib
import hmac
import secrets

ITERATIONS = 200000


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS
    ).hex()
    return "pbkdf2_sha256$%d$%s$%s" % (ITERATIONS, salt, digest)


def verify_password(password, stored):
    try:
        _algo, iterations, salt, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        ).hex()
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False
