"""Forced RadioReference system/site picker for V0.4D3K.

This module uses the SOAP signatures observed on the user's Pi:
  getCountryInfo(coid: int, authInfo)
  getStateInfo(stid: int, authInfo)
  getCountyInfo(ctid: int, authInfo)
  getTrsSites(sid: int, authInfo)

It intentionally avoids website scraping and only uses the official RR SOAP API.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any, Iterable

from .radioreference_import import RadioReferenceError, _client, _method_names, load_credentials

PARSER_MARKER = "forced-explicit-soap-v0.4d3k"
US_COUNTRY_ID = 2


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _plain(value: Any, depth: int = 0) -> Any:
    if depth > 80:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        from zeep.helpers import serialize_object  # type: ignore
        serialized = serialize_object(value, target_cls=dict)
        if serialized is not value:
            value = serialized
    except Exception:
        pass
    if isinstance(value, (dict, OrderedDict)):
        return {str(k): _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v, depth + 1) for v in value]
    if hasattr(value, "__values__"):
        try:
            return {str(k): _plain(v, depth + 1) for k, v in value.__values__.items()}
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return {str(k): _plain(v, depth + 1) for k, v in value.__dict__.items() if not str(k).startswith("_")}
        except Exception:
            pass
    return str(value)


def _as_list(value: Any) -> list[Any]:
    value = _plain(value)
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        # Common RR SOAP wrappers have a single nested list/object value.
        for key in (
            "state", "states", "stateInfo", "stateList",
            "county", "counties", "countyInfo", "countyList", "ctid",
            "trs", "trsInfo", "trsList", "systems", "system", "site", "siteList", "sites",
        ):
            if key in value:
                nested = _as_list(value.get(key))
                if nested:
                    return nested
        return [value]
    return [value]


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    value = _plain(value)
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _dig_lists(value: Any, key_tokens: tuple[str, ...]) -> list[Any]:
    value = _plain(value)
    found: list[Any] = []
    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, val in item.items():
                key_norm = _norm(key)
                if any(token in key_norm for token in key_tokens):
                    found.extend(_as_list(val))
                walk(val)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value)
    return [_plain(item) for item in found]


def _call(client: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    method = getattr(client.service, method_name)
    try:
        return method(**kwargs)
    except Exception as first:
        if args:
            try:
                return method(*args)
            except Exception:
                pass
        raise first


def _auth() -> tuple[Any, dict[str, str]]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    return _client(), creds.auth_info()


def _state_id_keys() -> tuple[str, ...]:
    return ("stid", "stateId", "state_id", "id")


def _county_id_keys() -> tuple[str, ...]:
    return ("ctid", "countyId", "county_id", "id")


def _system_id_keys() -> tuple[str, ...]:
    return ("sid", "trsId", "trsid", "systemId", "system_id", "id")


def _site_id_keys() -> tuple[str, ...]:
    return ("siteId", "siteID", "site_id", "siteNumber", "siteNo", "id")


def _first_id(item: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    lower = {str(k).lower(): k for k in item.keys()}
    for key in keys:
        original = lower.get(key.lower())
        if original is not None:
            num = _number(item.get(original))
            if num is not None:
                return num
    return None


def _blob(item: Any) -> str:
    try:
        return _norm(json.dumps(_plain(item), sort_keys=True, default=str))
    except Exception:
        return _norm(str(item))


def _name_from(item: dict[str, Any], fallback: str = "") -> str:
    for key in ("sName", "sysName", "systemName", "name", "descr", "description", "alphaTag", "tag", "countyName", "stateName", "siteName"):
        if key in item and _text(item.get(key)):
            return _text(item.get(key))
    return fallback


def _state_candidates(country_info: Any) -> list[dict[str, Any]]:
    raw = _dig_lists(country_info, ("statelist", "state list", "states"))
    candidates = [item for item in raw if isinstance(item, dict)]
    if not candidates:
        candidates = [item for item in _iter_dicts(country_info) if _first_id(item, _state_id_keys()) is not None and ("state" in _blob(item) or "az" in _blob(item))]
    deduped: dict[int, dict[str, Any]] = {}
    for item in candidates:
        sid = _first_id(item, _state_id_keys())
        if sid is not None:
            deduped.setdefault(sid, item)
    return list(deduped.values())


def _match_state(candidates: list[dict[str, Any]], state: str) -> tuple[int | None, dict[str, Any] | None]:
    wanted = _norm(state)
    aliases = {wanted}
    if wanted == "az":
        aliases.add("arizona")
    if wanted == "arizona":
        aliases.add("az")
    for item in candidates:
        item_blob = _blob(item)
        id_value = _first_id(item, _state_id_keys())
        if id_value is None:
            continue
        # Prefer exact-ish state code/name keys, then blob fallback.
        keyed = _norm(" ".join(_text(item.get(k)) for k in ("stateCode", "state_code", "code", "abbr", "stateName", "name", "state") if k in item))
        if any(alias and (alias == keyed or alias in keyed.split() or alias in keyed or alias in item_blob.split()) for alias in aliases):
            return id_value, item
    return None, None


def _county_candidates(state_info: Any, country_info: Any | None = None) -> list[dict[str, Any]]:
    raw: list[Any] = []
    for source in (state_info, country_info):
        if source is not None:
            raw.extend(_dig_lists(source, ("countylist", "county list", "counties", "county")))
    candidates = [item for item in raw if isinstance(item, dict)]
    if not candidates:
        candidates = [item for item in _iter_dicts(state_info) if _first_id(item, _county_id_keys()) is not None and ("county" in _blob(item) or "ctid" in _blob(item))]
    deduped: dict[int, dict[str, Any]] = {}
    for item in candidates:
        cid = _first_id(item, _county_id_keys())
        if cid is not None:
            deduped.setdefault(cid, item)
    return list(deduped.values())


def _match_county(candidates: list[dict[str, Any]], county: str) -> tuple[int | None, dict[str, Any] | None]:
    wanted = _norm(county).replace(" county", "").strip()
    for item in candidates:
        cid = _first_id(item, _county_id_keys())
        if cid is None:
            continue
        names = _norm(" ".join(_text(item.get(k)) for k in ("countyName", "county", "name", "ctName", "displayName") if k in item))
        item_blob = _blob(item)
        if wanted and (wanted in names or names in wanted or wanted in item_blob):
            return cid, item
    return None, None


def _system_candidates(*sources: Any, city: str = "", county: str = "") -> list[dict[str, Any]]:
    raw: list[Any] = []
    for source in sources:
        if source is None:
            continue
        raw.extend(_dig_lists(source, ("trslist", "trs list", "trunk", "systemlist", "systems")))
        raw.extend(_as_list(_plain(source).get("trsList") if isinstance(_plain(source), dict) else None))
    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in [x for x in raw if isinstance(x, dict)] + list(_iter_dicts(sources)):
        if not isinstance(item, dict):
            continue
        sid = _first_id(item, _system_id_keys())
        if sid is None or sid in seen:
            continue
        text_blob = _blob(item)
        # Avoid treating counties/states/agencies as trunked systems unless there is a system-like name/id field.
        has_system_key = any(str(k).lower() in {"sid", "trsid", "trs_id", "sname", "sysname", "systemname"} for k in item.keys())
        if not has_system_key and "trs" not in text_blob and "p25" not in text_blob and "trunk" not in text_blob:
            continue
        name = _name_from(item, f"RadioReference system {sid}")
        rank = 0
        city_norm = _norm(city)
        county_norm = _norm(county)
        if city_norm and city_norm in text_blob:
            rank += 50
        if county_norm and county_norm in text_blob:
            rank += 20
        for token in ("topaz", "trwc", "mesa", "gilbert", "maricopa", "regional", "wireless", "cooperative", "p25"):
            if token in text_blob:
                rank += 5
        candidates.append({
            "system_id": sid,
            "name": name,
            "site": _text(item.get("siteName") or item.get("site") or item.get("countyName") or item.get("county")),
            "type": _text(item.get("type") or item.get("trsType") or item.get("flavor") or item.get("systemType")),
            "county": _text(item.get("countyName") or item.get("county")),
            "state": _text(item.get("stateName") or item.get("state") or item.get("stateCode")),
            "rank": rank,
            "raw_keys": sorted(str(k) for k in item.keys())[:40],
        })
        seen.add(sid)
    candidates.sort(key=lambda item: (-int(item.get("rank") or 0), str(item.get("name") or ""), int(item.get("system_id") or 0)))
    return candidates[:75]


def _freq_to_hz(value: Any) -> int | None:
    text = _text(value).lower().replace("mhz", "").replace("hz", "").replace(",", "").strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except Exception:
        return None
    if numeric <= 0:
        return None
    return int(round(numeric * 1_000_000)) if numeric < 10000 else int(round(numeric))


def _extract_frequencies(item: Any) -> list[int]:
    freqs: list[int] = []
    for dct in _iter_dicts(item):
        for key, value in dct.items():
            key_norm = _norm(key)
            if "freq" in key_norm or key_norm in {"out", "outfreq", "cc"}:
                hz = _freq_to_hz(value)
                if hz and 20_000_000 <= hz <= 1_500_000_000 and hz not in freqs:
                    freqs.append(hz)
    return freqs


def discover_systems(payload: dict[str, Any]) -> dict[str, Any]:
    client, auth = _auth()
    state = _text(payload.get("state"))
    county = _text(payload.get("county"))
    city = _text(payload.get("city"))
    country_info = _plain(_call(client, "getCountryInfo", US_COUNTRY_ID, auth, coid=US_COUNTRY_ID, authInfo=auth))
    states = _state_candidates(country_info)
    state_id, state_match = _match_state(states, state)
    state_info = None
    counties: list[dict[str, Any]] = []
    county_id = None
    county_match = None
    county_info = None
    if state_id is not None:
        state_info = _plain(_call(client, "getStateInfo", state_id, auth, stid=state_id, authInfo=auth))
        counties = _county_candidates(state_info, country_info)
        county_id, county_match = _match_county(counties, county)
    if county_id is not None:
        county_info = _plain(_call(client, "getCountyInfo", county_id, auth, ctid=county_id, authInfo=auth))
    systems = _system_candidates(county_info, state_info, country_info, city=city, county=county)
    return {
        "ok": True,
        "picker_parser": PARSER_MARKER,
        "searched": {"state": state, "county": county, "city": city},
        "state_id": state_id,
        "state_match": None if state_match is None else {"id": state_id, "name": _name_from(state_match), "keys": sorted(str(k) for k in state_match.keys())},
        "county_id": county_id,
        "county_match": None if county_match is None else {"id": county_id, "name": _name_from(county_match), "keys": sorted(str(k) for k in county_match.keys())},
        "state_candidate_count": len(states),
        "county_candidate_count": len(counties),
        "system_count": len(systems),
        "systems": systems,
        "state_candidates_sample": [
            {"id": _first_id(item, _state_id_keys()), "name": _name_from(item), "keys": sorted(str(k) for k in item.keys())[:25]}
            for item in states[:12]
        ],
        "county_candidates_sample": [
            {"id": _first_id(item, _county_id_keys()), "name": _name_from(item), "keys": sorted(str(k) for k in item.keys())[:25]}
            for item in counties[:20]
        ],
        "country_top_keys": sorted(str(k) for k in country_info.keys()) if isinstance(country_info, dict) else [],
        "state_top_keys": sorted(str(k) for k in state_info.keys()) if isinstance(state_info, dict) else [],
        "county_top_keys": sorted(str(k) for k in county_info.keys()) if isinstance(county_info, dict) else [],
        "available_methods": _method_names(client),
        "hint": "Select a system from the dropdown, then load sites. City is used only for ranking; it does not filter regional systems.",
    }


def discover_sites(payload: dict[str, Any]) -> dict[str, Any]:
    client, auth = _auth()
    sid = _number(payload.get("system_id") or payload.get("sid"))
    if sid is None:
        raise RadioReferenceError("RadioReference System ID is required before loading sites")
    details = None
    sites_response = None
    try:
        details = _plain(_call(client, "getTrsDetails", sid, auth, sid=sid, authInfo=auth))
    except Exception:
        details = None
    try:
        sites_response = _plain(_call(client, "getTrsSites", sid, auth, sid=sid, authInfo=auth))
    except Exception as exc:
        sites_response = {"error": f"{type(exc).__name__}: {exc}"}
    raw_sites = _dig_lists(sites_response, ("sitelist", "site list", "sites", "site"))
    if not raw_sites:
        raw_sites = list(_iter_dicts(sites_response))
    sites: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_sites:
        if not isinstance(item, dict):
            continue
        site_id = _first_id(item, _site_id_keys())
        freqs = _extract_frequencies(item)
        name = _name_from(item, f"Site {site_id}" if site_id is not None else "Site")
        key = f"{site_id}:{name}:{freqs[:3]}"
        if key in seen:
            continue
        seen.add(key)
        if site_id is None and not freqs and len(raw_sites) > 1:
            continue
        sites.append({
            "site_id": site_id,
            "name": name,
            "label": f"{name}" + (f" ({len(freqs)} freqs)" if freqs else ""),
            "control_channels_hz": freqs,
            "control_channels_mhz": [f"{hz / 1_000_000:.6f}" for hz in freqs],
            "raw_keys": sorted(str(k) for k in item.keys())[:40],
        })
    if not sites:
        sites.append({"site_id": None, "name": "All/default sites", "label": "All/default sites", "control_channels_hz": [], "control_channels_mhz": [], "raw_keys": []})
    return {
        "ok": True,
        "picker_parser": PARSER_MARKER,
        "system_id": sid,
        "site_count": len(sites),
        "sites": sites,
        "sites_top_keys": sorted(str(k) for k in sites_response.keys()) if isinstance(sites_response, dict) else [],
        "details_top_keys": sorted(str(k) for k in details.keys()) if isinstance(details, dict) else [],
    }
