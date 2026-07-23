from __future__ import annotations

import secrets


def primary(address: str) -> tuple[str, str]:
    local, separator, domain = address.strip().lower().partition("@")
    if not separator or domain != "gmail.com":
        raise ValueError("GMAIL_ADDRESS must be a @gmail.com address")
    local = local.replace(".", "")
    if len(local) < 2:
        raise ValueError("Invalid Gmail address")
    return local, domain


def generate(address: str, kind: str) -> str:
    local, domain = primary(address)
    if kind == "dot":
        output = local[0]
        for character in local[1:]:
            output += ("." if secrets.choice((False, True)) else "") + character
        return f"{output}@{domain}"
    if kind in {"dotplus", "plus"}:
        return f"{local}+{secrets.token_urlsafe(5).lower()}@{domain}"
    if kind == "mixed":
        return generate(address, secrets.choice(("dot", "plus")))
    raise ValueError("type must be dot, dotplus, plus, or mixed")
