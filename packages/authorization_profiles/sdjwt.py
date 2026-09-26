"""Minimal SD-JWT (RFC 9901) primitives shared by the AP2 and VI profiles.

* Disclosures: ``[salt, name, value]`` for object properties, ``[salt, value]``
  for array elements; digest = ``b64url(sha-256(ascii(encoded_disclosure)))``.
* Placeholders: ``_sd: [digest…]`` in objects and ``{"...": digest}`` in arrays.
* Serialization: ``<issuer-jwt>~<disc>~…~`` (optionally followed by a KB-JWT).
* ``sd_hash`` binds a key-binding JWT to the exact presentation string it covers.

Signing uses joserfc (ES256 only, as both profiles require).
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from joserfc import jwt as _jwt
from joserfc.jwk import ECKey

from common.util import b64url_decode, b64url_encode, sha256_b64url

SD_ALG = "sha-256"


class SDJWTError(Exception):
    pass


# --------------------------------------------------------------- disclosures
@dataclass(frozen=True)
class Disclosure:
    encoded: str
    salt: str
    name: Optional[str]
    value: Any

    @property
    def digest(self) -> str:
        return sha256_b64url(self.encoded.encode("ascii"))

    @classmethod
    def for_property(cls, name: str, value: Any) -> "Disclosure":
        salt = b64url_encode(secrets.token_bytes(16))
        encoded = b64url_encode(json.dumps([salt, name, value], separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        return cls(encoded, salt, name, value)

    @classmethod
    def for_element(cls, value: Any) -> "Disclosure":
        salt = b64url_encode(secrets.token_bytes(16))
        encoded = b64url_encode(json.dumps([salt, value], separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        return cls(encoded, salt, None, value)

    @classmethod
    def parse(cls, encoded: str) -> "Disclosure":
        try:
            arr = json.loads(b64url_decode(encoded))
        except Exception as e:  # pragma: no cover - defensive
            raise SDJWTError("malformed disclosure: %s" % e)
        if not isinstance(arr, list) or len(arr) not in (2, 3):
            raise SDJWTError("disclosure must be a 2- or 3-element array")
        if len(arr) == 3:
            return cls(encoded, arr[0], arr[1], arr[2])
        return cls(encoded, arr[0], None, arr[1])


def element_ref(d: Disclosure) -> Dict[str, str]:
    return {"...": d.digest}


# ------------------------------------------------------------------ signing
def sign_jwt(header: Dict[str, Any], payload: Dict[str, Any], key: ECKey) -> str:
    h = dict(header)
    h.setdefault("alg", "ES256")
    return _jwt.encode(h, payload, key, algorithms=["ES256"])


def decode_unverified(token: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    parts = token.split(".")
    if len(parts) != 3:
        raise SDJWTError("not a compact JWS")
    header = json.loads(b64url_decode(parts[0]))
    payload = json.loads(b64url_decode(parts[1]))
    return header, payload


def verify_jwt(token: str, key: ECKey) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    try:
        tok = _jwt.decode(token, key, algorithms=["ES256"])
    except Exception as e:
        raise SDJWTError("signature verification failed: %s" % e.__class__.__name__)
    if tok.header.get("alg") != "ES256":
        raise SDJWTError("alg must be ES256")
    return dict(tok.header), dict(tok.claims)


# ------------------------------------------------------------ serialization
def serialize(issuer_jwt: str, disclosures: List[Disclosure], kb_jwt: Optional[str] = None) -> str:
    s = "~".join([issuer_jwt] + [d.encoded for d in disclosures]) + "~"
    if kb_jwt:
        s += kb_jwt
    return s


def parse(serialized: str) -> Tuple[str, List[Disclosure], Optional[str]]:
    parts = serialized.split("~")
    if len(parts) < 2:
        raise SDJWTError("not an SD-JWT presentation")
    issuer_jwt = parts[0]
    kb = parts[-1] or None
    disclosures = [Disclosure.parse(p) for p in parts[1:-1] if p]
    return issuer_jwt, disclosures, kb


def sd_hash(presentation_without_kb: str) -> str:
    """``sd_hash`` over the exact presentation string the KB-JWT covers."""
    return sha256_b64url(presentation_without_kb.encode("ascii"))


# ---------------------------------------------------------------- resolving
def resolve(payload: Any, disclosures: List[Disclosure]) -> Tuple[Any, Set[str]]:
    """Replace ``_sd`` / ``{"...": …}`` placeholders with disclosed values.

    Returns the resolved structure and the set of disclosure digests that were
    consumed. Un-referenced disclosures are reported by the caller (RFC 9901
    requires rejecting them)."""
    by_digest = {d.digest: d for d in disclosures}
    used: Set[str] = set()
    seen: Set[str] = set()

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: Dict[str, Any] = {}
            for k, v in node.items():
                if k == "_sd":
                    for dg in v:
                        d = by_digest.get(dg)
                        if d is None:
                            continue  # undisclosed – stays hidden
                        if d.name is None:
                            raise SDJWTError("array disclosure used as property")
                        if dg in seen:
                            raise SDJWTError("digest referenced twice")
                        seen.add(dg)
                        used.add(dg)
                        if d.name in out or d.name in node:
                            raise SDJWTError("duplicate claim %s" % d.name)
                        out[d.name] = _walk(d.value)
                elif k == "_sd_alg":
                    continue
                else:
                    out[k] = _walk(v)
            return out
        if isinstance(node, list):
            out_list = []
            for el in node:
                if isinstance(el, dict) and set(el.keys()) == {"..."}:
                    d = by_digest.get(el["..."])
                    if d is None:
                        continue  # undisclosed element
                    if d.name is not None:
                        raise SDJWTError("property disclosure used as array element")
                    if el["..."] in seen:
                        raise SDJWTError("digest referenced twice")
                    seen.add(el["..."])
                    used.add(el["..."])
                    out_list.append(_walk(d.value))
                else:
                    out_list.append(_walk(el))
            return out_list
        return node

    return _walk(payload), used


def resolve_strict(payload: Any, disclosures: List[Disclosure]) -> Any:
    resolved, used = resolve(payload, disclosures)
    unused = [d.digest for d in disclosures if d.digest not in used]
    if unused:
        raise SDJWTError("presentation contains %d disclosure(s) not referenced by the payload" % len(unused))
    return resolved


def check_sd_alg(payload: Dict[str, Any]) -> None:
    if payload.get("_sd_alg", SD_ALG) != SD_ALG:
        raise SDJWTError("unsupported _sd_alg")


def presentation_for(issuer_jwt: str, all_disclosures: List[Disclosure], chosen_digests: Set[str]) -> Tuple[str, List[Disclosure]]:
    """Select the disclosures to reveal (privacy: only what the verifier needs)."""
    chosen = [d for d in all_disclosures if d.digest in chosen_digests]
    return serialize(issuer_jwt, chosen), chosen
