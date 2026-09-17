"""Deterministic public identities for Bootstrap master-data commits."""

from __future__ import annotations

import hashlib


_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIXES = {
    "CUSTOMER": "CUS_",
    "SKU": "SKU_",
    "PRODUCT": "PRD_",
    "CHANNEL": "CHN_",
    "SALESREP": "REP_",
    "REGION": "REG_",
    "PROVINCE": "PRV_",
}


def _base32_80(value: bytes) -> str:
    """Encode exactly 80 bits with a fixed, unambiguous Crockford alphabet."""
    if len(value) != 10:
        raise ValueError("stable identity digest must contain exactly 80 bits")
    number = int.from_bytes(value, "big")
    chars = [""] * 16
    for index in range(15, -1, -1):
        chars[index] = _ALPHABET[number & 31]
        number >>= 5
    return "".join(chars)


def stable_id(entity_type: str, mdm_identity_key: str) -> str:
    """Return the approved MDM_ID_V1 deterministic stable identifier."""
    normalized_type = entity_type.strip().upper()
    if normalized_type not in _PREFIXES:
        raise ValueError(f"unsupported stable identity entity type: {entity_type}")
    if not mdm_identity_key or not mdm_identity_key.strip():
        raise ValueError("mdm_identity_key is required")
    identity_input = f"MDM_ID_V1|{normalized_type}|{mdm_identity_key.strip()}".encode("utf-8")
    return _PREFIXES[normalized_type] + _base32_80(hashlib.sha256(identity_input).digest()[:10])


def channel_identity(source_index: str) -> str:
    return f"CHANNEL:BOOTSTRAP:INDEX:{source_index}"


def historical_channel_identity(number: int) -> str:
    return f"CHANNEL:HIST:{number:03d}"


def salesrep_identity(row_number: int) -> str:
    return f"SALESREP:BOOTSTRAP:R{row_number}"


def customer_identity(row_number: int) -> str:
    return f"CUSTOMER:BOOTSTRAP:R{row_number}"


def product_identity(source_product_code: str) -> str:
    return f"PRODUCT:SOURCE_CODE:{source_product_code}"


def sku_identity(sku_code: str) -> str:
    return f"SKU:SOURCE_CODE:{sku_code}"

