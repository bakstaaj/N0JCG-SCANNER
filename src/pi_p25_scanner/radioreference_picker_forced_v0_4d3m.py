"""Forced RadioReference picker routes for PI-P25-SCANNER V0.4D3M.

This module deliberately uses the SOAP signatures observed on the target Pi:
  getCountryInfo(coid: int, authInfo)
  getStateInfo(stid: int, authInfo)
  getCountyInfo(ctid: int, authInfo)
  getTrsSites(sid: int, authInfo)

It returns UI-friendly system/site lists without requiring manual numeric lookup.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any
import re

from .radioreference_import import RadioReferenceError, _client, _method_names, load_credentials

PARSER_MARKER = "us-country-explicit-soap-v0.4d3m"
US_COUNTRY_COIDS = [1, 2]  # 1 normally returns United States; 2 returned Canada on the target Pi probe.

STATE_ALIASES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa",
    "ks": "kansas", "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri",
    "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey",
    "nm": "new mexico", "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio",
    "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont",
    "va": "virginia", "wa": "washington", "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
    "dc": "district of columbia",
}
STATE_NAMES_TO_CODE = {name: code for code, name in STATE_ALIASES.items()}


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except Exception:
        return None


def _to_plain(value: Any, depth: int = 0) -> Any:
    if depth > 40:
        return str(type(value).__name__)
    try:
        from zeep.helpers import serialize_object  # type: ignore
        value = serialize_object(value, target_cls=OrderedDict)
    except Exception:
        pass
    if isinstance(value, OrderedDict):
        value = dict(value)
    if isinstance(value, dict):
        return {str(k): _to_plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain(v, depth + 1) for v in value]
    if hasattr(value, "__values__"):
        try:
            return {str(k): _to_plain(v, depth + 1) for k, v in value.__values__.items()}
        except Exception:
            pass
    if hasattr(value, "__dict__") and not isinstance(value, (str, bytes, int, float, bool)):
        try:
            return {str(k): _to_plain(v, depth + 1) for k, v in vars(value).items() if not str(k).startswith("_")}
        except Exception:
            pass
    return value


def _iter_dicts(value: Any):
    value = _to_plain(value)
    stack = [value]
    seen = 0
    while stack and seen < 20000:
        seen += 1
        item = stack.pop()
        if isinstance(item, dict):
            yield item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _get_any(item: dict[str, Any], *keys: str) -> Any:
    lookup = {_normalize(k): v for k, v in item.items()}
    for key in keys:
        key_norm = _normalize(key)
        if key_norm in lookup:
            return lookup[key_norm]
    return None


def _unwrap_named_lists(root: Any, *names: str) -> list[Any]:
    wanted = {_normalize(name) for name in names}
    out: list[Any] = []
    for item in _iter_dicts(root):
        for key, value in item.items():
            if _normalize(key) not in wanted:
                continue
            plain = _to_plain(value)
            if isinstance(plain, list):
                out.extend(plain)
            elif isinstance(plain, dict):
                # Some Zeep responses wrap the actual list one level deeper.
                child_lists = [v for v in plain.values() if isinstance(_to_plain(v), list)]
                if child_lists:
                    for child in child_lists:
                        out.extend(_to_plain(child))
                else:
                    out.append(plain)
            elif plain not in (None, ""):
                out.append(plain)
    return out


def _candidate_dicts(root: Any, list_names: tuple[str, ...], id_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for value in _unwrap_named_lists(root, *list_names):
        if isinstance(value, dict):
            candidates.append(value)
        else:
            for item in _iter_dicts(value):
                candidates.append(item)
    # Fall back to any nested dict that has one of the ID keys.
    if not candidates:
        id_norms = {_normalize(k) for k in id_keys}
        for item in _iter_dicts(root):
            if any(_normalize(k) in id_norms for k in item.keys()):
                candidates.append(item)
    # De-dupe by object text; keep order.
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        key = repr(sorted((str(k), str(v)[:80]) for k, v in item.items()))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _state_query_terms(state: str) -> set[str]:
    state_norm = _normalize(state)
    terms = {state_norm} if state_norm else set()
    compact = state_norm.replace(" ", "")
    if len(compact) == 2 and compact in STATE_ALIASES:
        terms.add(STATE_ALIASES[compact])
        terms.add(compact)
    if state_norm in STATE_NAMES_TO_CODE:
        terms.add(STATE_NAMES_TO_CODE[state_norm])
        terms.add(state_norm)
    return {t for t in terms if t}


def _find_state(country_info: Any, state: str) -> tuple[int | None, dict[str, Any] | None, list[dict[str, Any]]]:
    terms = _state_query_terms(state)
    candidates = _candidate_dicts(country_info, ("stateList", "states", "state"), ("stid", "stateId", "id"))
    matched: dict[str, Any] | None = None
    for item in candidates:
        stid = _number(_get_any(item, "stid", "stateId", "state_id", "id"))
        if stid is None:
            continue
        code = _normalize(_get_any(item, "stateCode", "state_code", "code", "abbr", "abbreviation"))
        name = _normalize(_get_any(item, "stateName", "state_name", "name", "state"))
        blob = _normalize(" ".join(str(v) for v in item.values()))
        if terms and (code in terms or name in terms or any(term in blob for term in terms)):
            matched = item
            return stid, matched, candidates[:25]
    return None, matched, candidates[:25]


def _find_county(state_info: Any, county: str) -> tuple[int | None, dict[str, Any] | None, list[dict[str, Any]]]:
    county_norm = _normalize(county)
    candidates = _candidate_dicts(state_info, ("countyList", "counties", "county"), ("ctid", "countyId", "id"))
    for item in candidates:
        ctid = _number(_get_any(item, "ctid", "countyId", "county_id", "id"))
        if ctid is None:
            continue
        name = _normalize(_get_any(item, "countyName", "county_name", "name", "county"))
        blob = _normalize(" ".join(str(v) for v in item.values()))
        if county_norm and (county_norm == name or county_norm in name or county_norm in blob):
            return ctid, item, candidates[:25]
    return None, None, candidates[:25]


def _system_id(item: dict[str, Any]) -> int | None:
    return _number(_get_any(item, "sid", "trsId", "trs_id", "systemId", "system_id", "id"))


def _system_name(item: dict[str, Any]) -> str:
    return _text(_get_any(item, "sName", "sysName", "systemName", "name", "descr", "description", "label"))


def _site_id(item: dict[str, Any]) -> int | None:
    return _number(_get_any(item, "siteId", "site_id", "siteNumber", "siteNo", "id", "sid"))


def _site_name(item: dict[str, Any]) -> str:
    return _text(_get_any(item, "siteName", "site_name", "name", "descr", "description", "label", "rfss"))


def _freq_to_hz(value: Any) -> int | None:
    text = str(value or "").strip().lower().replace("mhz", "").replace("hz", "").replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except Exception:
        return None
    if number <= 0:
        return None
    return int(round(number * 1_000_000)) if number < 10000 else int(round(number))


def _extract_freqs(item: Any) -> list[int]:
    freqs: list[int] = []
    for d in _iter_dicts(item):
        for key, value in d.items():
            key_norm = _normalize(key)
            if "freq" in key_norm or key_norm in {"out", "out freq", "cc"}:
                hz = _freq_to_hz(value)
                if hz is not None and 20_000_000 <= hz <= 1_500_000_000 and hz not in freqs:
                    freqs.append(hz)
    return freqs


def _rank_system(item: dict[str, Any], city: str, county: str) -> tuple[int, str]:
    blob = _normalize(" ".join(str(v) for v in item.values()))
    score = 0
    if _normalize(city) and _normalize(city) in blob:
        score -= 100
    if _normalize(county) and _normalize(county) in blob:
        score -= 25
    name = _system_name(item).lower()
    return (score, name)


def _auth() -> dict[str, str]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    return creds.auth_info()


def find_systems(payload: dict[str, Any]) -> dict[str, Any]:
    state = _text(payload.get("state"))
    county = _text(payload.get("county"))
    city = _text(payload.get("city"))
    auth = _auth()
    client = _client()
    call_errors: list[dict[str, str]] = []
    country_info: Any = None
    state_info: Any = None
    county_info: Any = None
    state_id: int | None = None
    county_id: int | None = None
    state_match: dict[str, Any] | None = None
    county_match: dict[str, Any] | None = None
    state_candidates: list[dict[str, Any]] = []
    county_candidates: list[dict[str, Any]] = []

    country_coid: int | None = None
    country_attempts: list[dict[str, Any]] = []
    for coid in US_COUNTRY_COIDS:
        try:
            candidate_country = client.service.getCountryInfo(coid=coid, authInfo=auth)
            candidate_state_id, candidate_state_match, candidate_state_candidates = _find_state(candidate_country, state)
            country_attempts.append({
                "coid": coid,
                "state_id": candidate_state_id,
                "state_candidate_count": len(candidate_state_candidates),
                "first_state_candidates": [_safe_preview(v) for v in candidate_state_candidates[:4]],
            })
            if candidate_state_id is not None:
                country_info = candidate_country
                country_coid = coid
                state_id = candidate_state_id
                state_match = candidate_state_match
                state_candidates = candidate_state_candidates
                break
            if country_info is None:
                country_info = candidate_country
                state_candidates = candidate_state_candidates
        except Exception as exc:
            call_errors.append({"method": f"getCountryInfo coid={coid}", "error": f"{type(exc).__name__}: {exc}"})

    if state_id is not None:
        try:
            state_info = client.service.getStateInfo(stid=state_id, authInfo=auth)
            county_id, county_match, county_candidates = _find_county(state_info, county)
        except Exception as exc:
            call_errors.append({"method": "getStateInfo", "error": f"{type(exc).__name__}: {exc}"})

    if county_id is not None:
        try:
            county_info = client.service.getCountyInfo(ctid=county_id, authInfo=auth)
        except Exception as exc:
            call_errors.append({"method": "getCountyInfo", "error": f"{type(exc).__name__}: {exc}"})

    source = county_info or state_info or country_info
    system_candidates = _candidate_dicts(source, ("trsList", "trunkedSystems", "trunkedSystemList", "systems", "systemList", "trs"), ("sid", "trsId", "systemId"))
    systems_by_id: dict[int, dict[str, Any]] = {}
    for item in system_candidates:
        sid = _system_id(item)
        if sid is None:
            continue
        name = _system_name(item) or f"RadioReference System {sid}"
        blob = _normalize(" ".join(str(v) for v in item.values()))
        systems_by_id.setdefault(sid, {
            "system_id": sid,
            "name": name,
            "label": f"{name} (RR {sid})",
            "site": _text(_get_any(item, "site", "siteName", "countyName", "county", "stateName")),
            "rank_hint": "city" if _normalize(city) and _normalize(city) in blob else ("county" if _normalize(county) and _normalize(county) in blob else "state/county"),
            "raw_keys": sorted(str(k) for k in item.keys())[:30],
        })
    systems = sorted(systems_by_id.values(), key=lambda row: _rank_system(row, city, county))[:100]
    return {
        "ok": True,
        "picker_parser": PARSER_MARKER,
        "searched": {"state": state, "county": county, "city": city},
        "country_coid": country_coid,
        "country_attempts": country_attempts,
        "state_id": state_id,
        "county_id": county_id,
        "state_match": _safe_preview(state_match),
        "county_match": _safe_preview(county_match),
        "system_count": len(systems),
        "systems": systems,
        "available_methods": _method_names(client),
        "call_errors_sample": call_errors[:12],
        "state_candidates_sample": [_safe_preview(v) for v in state_candidates[:8]],
        "county_candidates_sample": [_safe_preview(v) for v in county_candidates[:8]],
        "system_candidates_sample": [_safe_preview(v) for v in system_candidates[:8]],
        "hint": "Select a system from the dropdown, then load sites. City is used only for ranking; county/state systems are still listed.",
    }


def _safe_preview(value: Any) -> Any:
    plain = _to_plain(value)
    if isinstance(plain, dict):
        return {str(k): _short(v) for k, v in list(plain.items())[:25]}
    if isinstance(plain, list):
        return [_safe_preview(v) for v in plain[:8]]
    return _short(plain)


def _short(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = str(value) if isinstance(value, str) else value
        return text[:240] if isinstance(text, str) else text
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return {str(k): _short(v) for k, v in list(value.items())[:10]}
    return str(value)[:240]


def find_sites(payload: dict[str, Any]) -> dict[str, Any]:
    sid = _number(payload.get("system_id") or payload.get("sid"))
    if sid is None:
        raise RadioReferenceError("RR System ID is required before loading sites")
    auth = _auth()
    client = _client()
    try:
        raw = client.service.getTrsSites(sid=sid, authInfo=auth)
    except Exception as exc:
        raise RadioReferenceError(f"RadioReference getTrsSites call failed: {type(exc).__name__}: {exc}") from exc
    site_candidates = _candidate_dicts(raw, ("siteList", "sites", "site"), ("siteId", "siteNumber", "id"))
    by_id: dict[int, dict[str, Any]] = {}
    fallback_id = 0
    for item in site_candidates:
        site_id = _site_id(item)
        if site_id is None:
            fallback_id -= 1
            site_id = fallback_id
        name = _site_name(item) or f"Site {site_id}"
        freqs = _extract_freqs(item)
        by_id.setdefault(site_id, {
            "site_id": None if site_id < 0 else site_id,
            "name": name,
            "label": f"{name}" + (f" (RR Site {site_id})" if site_id >= 0 else ""),
            "control_channels_hz": freqs,
            "control_channels_mhz": [f"{hz / 1_000_000:.6f}" for hz in freqs],
            "raw_keys": sorted(str(k) for k in item.keys())[:30],
        })
    sites = list(by_id.values())[:200]
    return {
        "ok": True,
        "picker_parser": PARSER_MARKER,
        "system_id": sid,
        "site_count": len(sites),
        "sites": sites,
        "site_candidates_sample": [_safe_preview(v) for v in site_candidates[:8]],
    }
