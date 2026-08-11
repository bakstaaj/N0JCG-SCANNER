"""Runtime RadioReference system/site picker helpers for scanner.

This module intentionally uses explicit SOAP method signatures observed from the
RadioReference WSDL instead of relying on older loose positional call guesses.
It never stores or exposes secrets; credentials are loaded from the existing
Pi-local runtime/settings/radioreference.env file.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable

from .radioreference_import import RadioReferenceError, _client, load_credentials

STATE_ALIASES = {
    "al": ["al", "alabama"], "ak": ["ak", "alaska"], "az": ["az", "arizona"], "ar": ["ar", "arkansas"],
    "ca": ["ca", "california"], "co": ["co", "colorado"], "ct": ["ct", "connecticut"], "de": ["de", "delaware"],
    "fl": ["fl", "florida"], "ga": ["ga", "georgia"], "hi": ["hi", "hawaii"], "id": ["id", "idaho"],
    "il": ["il", "illinois"], "in": ["in", "indiana"], "ia": ["ia", "iowa"], "ks": ["ks", "kansas"],
    "ky": ["ky", "kentucky"], "la": ["la", "louisiana"], "me": ["me", "maine"], "md": ["md", "maryland"],
    "ma": ["ma", "massachusetts"], "mi": ["mi", "michigan"], "mn": ["mn", "minnesota"], "ms": ["ms", "mississippi"],
    "mo": ["mo", "missouri"], "mt": ["mt", "montana"], "ne": ["ne", "nebraska"], "nv": ["nv", "nevada"],
    "nh": ["nh", "new hampshire"], "nj": ["nj", "new jersey"], "nm": ["nm", "new mexico"], "ny": ["ny", "new york"],
    "nc": ["nc", "north carolina"], "nd": ["nd", "north dakota"], "oh": ["oh", "ohio"], "ok": ["ok", "oklahoma"],
    "or": ["or", "oregon"], "pa": ["pa", "pennsylvania"], "ri": ["ri", "rhode island"], "sc": ["sc", "south carolina"],
    "sd": ["sd", "south dakota"], "tn": ["tn", "tennessee"], "tx": ["tx", "texas"], "ut": ["ut", "utah"],
    "vt": ["vt", "vermont"], "va": ["va", "virginia"], "wa": ["wa", "washington"], "wv": ["wv", "west virginia"],
    "wi": ["wi", "wisconsin"], "wy": ["wy", "wyoming"], "dc": ["dc", "district of columbia"],
}


def _normalize(value: Any) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _state_aliases(value: Any) -> set[str]:
    norm = _normalize(value)
    if not norm:
        return set()
    for aliases in STATE_ALIASES.values():
        if norm in aliases:
            return set(aliases)
    return {norm}


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


def _plain(value: Any) -> Any:
    """Convert Zeep/OrderedDict/nested response objects to plain containers."""
    try:
        from zeep.helpers import serialize_object  # type: ignore

        value = serialize_object(value, target_cls=OrderedDict)
    except Exception:
        pass
    if isinstance(value, OrderedDict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if hasattr(value, "__values__"):
        try:
            return {str(k): _plain(v) for k, v in value.__values__.items()}
        except Exception:
            pass
    if hasattr(value, "__dict__") and not isinstance(value, (str, bytes, bytearray)):
        try:
            return {str(k): _plain(v) for k, v in vars(value).items() if not str(k).startswith("_")}
        except Exception:
            pass
    return value


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    value = _plain(value)
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _as_list(value: Any) -> list[Any]:
    value = _plain(value)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # RadioReference SOAP list wrappers often look like {"state": [...]} or {"County": [...]}.
        for key in ("state", "State", "county", "County", "agency", "Agency", "trs", "TRS", "site", "Site", "item", "Item"):
            child = value.get(key)
            if child is not None:
                items = _as_list(child)
                if items:
                    return items
        if len(value) == 1:
            only = next(iter(value.values()))
            items = _as_list(only)
            if items:
                return items
        return [value]
    return [value]


def _children_named(value: Any, names: set[str]) -> list[Any]:
    found: list[Any] = []
    wanted = {_normalize(name).replace(" ", "") for name in names}
    for item in _iter_dicts(value):
        for key, child in item.items():
            key_norm = _normalize(key).replace(" ", "")
            if key_norm in wanted:
                found.extend(_as_list(child))
    return found


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lower = {str(k).lower(): k for k in item.keys()}
    compact = {_normalize(k).replace(" ", ""): k for k in item.keys()}
    for key in keys:
        if key in item:
            return item[key]
        lk = key.lower()
        if lk in lower:
            return item[lower[lk]]
        ck = _normalize(key).replace(" ", "")
        if ck in compact:
            return item[compact[ck]]
    return None


def _method_names(client: Any) -> list[str]:
    names: set[str] = set()
    try:
        for service in client.wsdl.services.values():
            for port in service.ports.values():
                names.update(port.binding._operations.keys())
    except Exception:
        pass
    return sorted(names)


def _call_keyword_then_positional(method: Any, kwargs: dict[str, Any], args: tuple[Any, ...]) -> Any:
    last_error: Exception | None = None
    try:
        return method(**kwargs)
    except Exception as exc:
        last_error = exc
    try:
        return method(*args)
    except Exception as exc:
        last_error = exc
    raise RadioReferenceError(f"RadioReference call failed: {type(last_error).__name__}: {last_error}")


def _get_country_info(client: Any, auth: dict[str, str]) -> Any:
    # WSDL signature observed on Jim's Pi: getCountryInfo(coid: int, authInfo: authInfo)
    return _call_keyword_then_positional(client.service.getCountryInfo, {"coid": 2, "authInfo": auth}, (2, auth))


def _get_state_info(client: Any, stid: int, auth: dict[str, str]) -> Any:
    return _call_keyword_then_positional(client.service.getStateInfo, {"stid": int(stid), "authInfo": auth}, (int(stid), auth))


def _get_county_info(client: Any, ctid: int, auth: dict[str, str]) -> Any:
    return _call_keyword_then_positional(client.service.getCountyInfo, {"ctid": int(ctid), "authInfo": auth}, (int(ctid), auth))


def _get_trs_details(client: Any, sid: int, auth: dict[str, str]) -> Any:
    return _call_keyword_then_positional(client.service.getTrsDetails, {"sid": int(sid), "authInfo": auth}, (int(sid), auth))


def _get_trs_sites(client: Any, sid: int, auth: dict[str, str]) -> Any:
    return _call_keyword_then_positional(client.service.getTrsSites, {"sid": int(sid), "authInfo": auth}, (int(sid), auth))


def _candidate_name(item: dict[str, Any]) -> str:
    return _text(_first_value(item, ("name", "sName", "sysName", "systemName", "descr", "description", "label", "alphaTag", "siteName", "siteDescr", "countyName", "stateName")))


def _state_id_from_country(country_info: Any, state_query: str) -> tuple[int | None, list[dict[str, Any]]]:
    aliases = _state_aliases(state_query)
    state_items = _children_named(country_info, {"stateList", "states", "state"})
    if not state_items:
        state_items = [item for item in _iter_dicts(country_info) if _number(_first_value(item, ("stid", "stateId", "state_id", "id"))) is not None]
    candidates: list[dict[str, Any]] = []
    for raw in state_items:
        if not isinstance(_plain(raw), dict):
            continue
        item = _plain(raw)
        stid = _number(_first_value(item, ("stid", "stateId", "state_id", "id")))
        code = _text(_first_value(item, ("stateCode", "state_code", "code", "abbr", "abbrev")))
        name = _text(_first_value(item, ("stateName", "state_name", "name", "descr", "description")))
        if stid is None:
            continue
        entry = {"state_id": stid, "code": code, "name": name, "raw_keys": sorted(str(k) for k in item.keys())[:20]}
        candidates.append(entry)
        values = {_normalize(code), _normalize(name)}
        if aliases and values.intersection(aliases):
            return stid, candidates
    # Fallback: substring match for values like "Arizona (AZ)".
    for entry in candidates:
        haystack = _normalize(f"{entry.get('code')} {entry.get('name')}")
        if aliases and any(alias in haystack for alias in aliases):
            return _number(entry.get("state_id")), candidates
    return None, candidates


def _county_id_from_state(state_info: Any, county_query: str) -> tuple[int | None, list[dict[str, Any]]]:
    wanted = _normalize(county_query).replace(" county", "")
    county_items = _children_named(state_info, {"countyList", "counties", "county"})
    if not county_items:
        county_items = [item for item in _iter_dicts(state_info) if _number(_first_value(item, ("ctid", "countyId", "county_id", "coid", "id"))) is not None]
    candidates: list[dict[str, Any]] = []
    for raw in county_items:
        if not isinstance(_plain(raw), dict):
            continue
        item = _plain(raw)
        ctid = _number(_first_value(item, ("ctid", "countyId", "county_id", "id")))
        name = _text(_first_value(item, ("countyName", "county_name", "name", "descr", "description")))
        if ctid is None:
            continue
        entry = {"county_id": ctid, "name": name, "raw_keys": sorted(str(k) for k in item.keys())[:20]}
        candidates.append(entry)
        norm_name = _normalize(name).replace(" county", "")
        if wanted and (norm_name == wanted or wanted in norm_name or norm_name in wanted):
            return ctid, candidates
    return None, candidates


def _extract_frequency_hz(value: Any) -> list[int]:
    freqs: list[int] = []
    for item in _iter_dicts(value):
        for key, raw in item.items():
            key_norm = _normalize(key)
            if "freq" not in key_norm and key_norm not in {"out", "outfreq", "out freq"}:
                continue
            if raw is None or isinstance(raw, bool):
                continue
            text = str(raw).lower().replace("mhz", "").replace("hz", "").replace(",", "").strip()
            try:
                numeric = float(text)
            except Exception:
                continue
            hz = int(round(numeric * 1_000_000)) if numeric < 10000 else int(round(numeric))
            if 20_000_000 <= hz <= 1_500_000_000 and hz not in freqs:
                freqs.append(hz)
    return freqs


def _systems_from_county_info(county_info: Any, city_query: str = "") -> list[dict[str, Any]]:
    items = _children_named(county_info, {"trsList", "trunkedSystems", "trunkedSystemList", "systems", "systemList", "trs"})
    if not items:
        items = list(_iter_dicts(county_info))
    city_norm = _normalize(city_query)
    deduped: dict[int, dict[str, Any]] = {}
    for raw in items:
        item = _plain(raw)
        if not isinstance(item, dict):
            continue
        sid = _number(_first_value(item, ("sid", "trsId", "systemId", "system_id", "id")))
        if sid is None or sid <= 0:
            continue
        name = _candidate_name(item) or f"RadioReference System {sid}"
        flavor = _text(_first_value(item, ("flavor", "type", "sysType", "systemType")))
        location_text = " ".join(_text(v) for v in item.values() if isinstance(v, (str, int, float)))
        rank = 0
        if city_norm and city_norm in _normalize(location_text):
            rank -= 50
        if "topaz" in _normalize(name) or "trwc" in _normalize(name):
            rank -= 25
        entry = {
            "system_id": sid,
            "name": name,
            "label": f"{name} — RR System {sid}",
            "type": flavor,
            "rank": rank,
            "raw_keys": sorted(str(k) for k in item.keys())[:24],
        }
        if sid not in deduped or entry["rank"] < deduped[sid].get("rank", 0):
            deduped[sid] = entry
    return sorted(deduped.values(), key=lambda x: (int(x.get("rank", 0)), str(x.get("name", ""))))[:50]


def radioreference_picker_systems(payload: dict[str, Any]) -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    client = _client()
    auth = creds.auth_info()
    state = _text(payload.get("state"))
    county = _text(payload.get("county"))
    city = _text(payload.get("city"))
    country_info = _get_country_info(client, auth)
    state_id, state_candidates = _state_id_from_country(country_info, state)
    county_id = None
    county_candidates: list[dict[str, Any]] = []
    systems: list[dict[str, Any]] = []
    state_info_plain: Any = None
    county_info_plain: Any = None
    if state_id is not None:
        state_info = _get_state_info(client, int(state_id), auth)
        state_info_plain = _plain(state_info)
        county_id, county_candidates = _county_id_from_state(state_info_plain, county)
        if county_id is not None:
            county_info = _get_county_info(client, int(county_id), auth)
            county_info_plain = _plain(county_info)
            systems = _systems_from_county_info(county_info_plain, city)
        else:
            systems = _systems_from_county_info(state_info_plain, city)
    return {
        "ok": True,
        "picker_version": "v0.4d3j-explicit-soap-signatures",
        "searched": {"state": state, "county": county, "city": city},
        "state_id": state_id,
        "county_id": county_id,
        "state_candidates_sample": state_candidates[:10],
        "county_candidates_sample": county_candidates[:20],
        "system_count": len(systems),
        "systems": systems,
        "source_summaries": [
            {"name": "getCountryInfo", "keys_sample": sorted(str(k) for k in _plain(country_info).keys())[:20] if isinstance(_plain(country_info), dict) else [], "state_candidate_count": len(state_candidates)},
            {"name": "getStateInfo", "keys_sample": sorted(str(k) for k in state_info_plain.keys())[:20] if isinstance(state_info_plain, dict) else [], "county_candidate_count": len(county_candidates)},
            {"name": "getCountyInfo", "keys_sample": sorted(str(k) for k in county_info_plain.keys())[:20] if isinstance(county_info_plain, dict) else [], "system_count": len(systems)},
        ],
        "available_methods": _method_names(client),
        "hint": "Select a system from the dropdown, then load sites. City is used for ranking, not filtering.",
    }


def _site_id(item: dict[str, Any]) -> int | None:
    return _number(_first_value(item, ("siteId", "site_id", "siteNumber", "siteNo", "site", "id")))


def radioreference_picker_sites(payload: dict[str, Any]) -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    system_id = _number(payload.get("system_id") or payload.get("sid"))
    if system_id is None:
        raise RadioReferenceError("RR System ID is required before loading sites")
    client = _client()
    auth = creds.auth_info()
    details_plain = _plain(_get_trs_details(client, int(system_id), auth))
    try:
        sites_plain = _plain(_get_trs_sites(client, int(system_id), auth))
    except Exception:
        sites_plain = details_plain
    site_items = _children_named(sites_plain, {"siteList", "sites", "site"}) or list(_iter_dicts(sites_plain))
    deduped: dict[str, dict[str, Any]] = {}
    for raw in site_items:
        item = _plain(raw)
        if not isinstance(item, dict):
            continue
        freqs = _extract_frequency_hz(item)
        sid = _site_id(item)
        name = _candidate_name(item)
        if sid is None and not name and not freqs:
            continue
        key = str(sid) if sid is not None else f"name:{name}:{','.join(map(str, freqs[:3]))}"
        if key in deduped:
            continue
        label_parts = [name or "Unnamed site"]
        if sid is not None:
            label_parts.append(f"Site {sid}")
        if freqs:
            label_parts.append(", ".join(f"{hz / 1_000_000:.6f}" for hz in freqs[:4]))
        deduped[key] = {
            "site_id": sid,
            "name": name or "Unnamed site",
            "label": " — ".join(label_parts),
            "control_channels_hz": freqs,
            "raw_keys": sorted(str(k) for k in item.keys())[:24],
        }
    sites = sorted(deduped.values(), key=lambda x: (x.get("site_id") is None, x.get("site_id") or 999999, str(x.get("name", ""))))
    return {
        "ok": True,
        "picker_version": "v0.4d3j-explicit-soap-signatures",
        "system_id": system_id,
        "site_count": len(sites),
        "sites": sites[:100],
        "details_keys": sorted(str(k) for k in details_plain.keys())[:30] if isinstance(details_plain, dict) else [],
        "sites_keys": sorted(str(k) for k in sites_plain.keys())[:30] if isinstance(sites_plain, dict) else [],
    }
