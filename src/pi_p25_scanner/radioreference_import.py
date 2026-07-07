"""RadioReference official SOAP Web Service integration.

This module uses the documented RadioReference SOAP2 API. It does not scrape the
RadioReference website and it never ships credentials in source control.
Credentials are stored locally on the Pi under runtime/settings/radioreference.env
with mode 0600.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_DIR = PROJECT_ROOT / "runtime" / "settings"
RR_ENV_PATH = SETTINGS_DIR / "radioreference.env"
RR_WSDL_URL = os.environ.get("P25_RR_WSDL_URL", "https://api.radioreference.com/soap2/?wsdl&v=latest&s=rpc")
RR_VERSION = os.environ.get("P25_RR_VERSION", "latest")
RR_STYLE = os.environ.get("P25_RR_STYLE", "rpc")

CATEGORY_SYNONYMS = {
    "fire": "Fire",
    "ems": "EMS",
    "emergency medical": "EMS",
    "law": "Law Enforcement",
    "law enforcement": "Law Enforcement",
    "police": "Law Enforcement",
    "sheriff": "Law Enforcement",
    "public works": "Public Works",
    "utilities": "Utilities",
    "utility": "Utilities",
    "transportation": "Transportation",
    "interop": "Interop",
    "interoperability": "Interop",
    "emergency management": "Emergency Management",
    "oem": "Emergency Management",
    "corrections": "Corrections",
    "jail": "Corrections",
    "schools": "Schools",
    "school": "Schools",
    "federal": "Federal",
}


class RadioReferenceError(RuntimeError):
    """Raised when RadioReference import cannot complete."""


@dataclass(slots=True)
class RadioReferenceCredentials:
    app_key: str = ""
    username: str = ""
    password: str = ""
    version: str = RR_VERSION
    style: str = RR_STYLE

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.username and self.password)

    def auth_info(self) -> dict[str, str]:
        return {
            "appKey": self.app_key,
            "username": self.username,
            "password": self.password,
            "version": self.version or RR_VERSION,
            "style": self.style or RR_STYLE,
        }


def _quote_env(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_credentials() -> RadioReferenceCredentials:
    env_values = _parse_env_file(RR_ENV_PATH)
    return RadioReferenceCredentials(
        app_key=os.environ.get("RADIOREFERENCE_APP_KEY") or os.environ.get("RR_APP_KEY") or env_values.get("RADIOREFERENCE_APP_KEY", ""),
        username=os.environ.get("RADIOREFERENCE_USERNAME") or os.environ.get("RR_USERNAME") or env_values.get("RADIOREFERENCE_USERNAME", ""),
        password=os.environ.get("RADIOREFERENCE_PASSWORD") or os.environ.get("RR_PASSWORD") or env_values.get("RADIOREFERENCE_PASSWORD", ""),
        version=os.environ.get("RADIOREFERENCE_VERSION") or env_values.get("RADIOREFERENCE_VERSION", RR_VERSION),
        style=os.environ.get("RADIOREFERENCE_STYLE") or env_values.get("RADIOREFERENCE_STYLE", RR_STYLE),
    )


def save_credentials(payload: dict[str, Any]) -> dict[str, Any]:
    app_key = str(payload.get("app_key") or payload.get("appKey") or payload.get("RADIOREFERENCE_APP_KEY") or "").strip()
    username = str(payload.get("username") or payload.get("RADIOREFERENCE_USERNAME") or "").strip()
    password = str(payload.get("password") or payload.get("RADIOREFERENCE_PASSWORD") or "")
    version = str(payload.get("version") or RR_VERSION).strip() or RR_VERSION
    style = str(payload.get("style") or RR_STYLE).strip() or RR_STYLE
    if not app_key or not username or not password:
        raise RadioReferenceError("RadioReference app key, username, and password are required")
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        [
            "# Local RadioReference credentials for PI-P25-SCANNER.",
            "# This file must stay local on the Pi and must not be committed.",
            f"RADIOREFERENCE_APP_KEY={_quote_env(app_key)}",
            f"RADIOREFERENCE_USERNAME={_quote_env(username)}",
            f"RADIOREFERENCE_PASSWORD={_quote_env(password)}",
            f"RADIOREFERENCE_VERSION={_quote_env(version)}",
            f"RADIOREFERENCE_STYLE={_quote_env(style)}",
            "",
        ]
    )
    RR_ENV_PATH.write_text(text, encoding="utf-8")
    try:
        RR_ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return radioreference_status()


def zeep_status() -> dict[str, Any]:
    try:
        import zeep  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on Pi environment
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    version = getattr(zeep, "__version__", "unknown")
    return {"available": True, "version": version}


def radioreference_status() -> dict[str, Any]:
    creds = load_credentials()
    return {
        "ok": True,
        "configured": creds.configured,
        "credentials_path": str(RR_ENV_PATH),
        "username": creds.username if creds.username else "",
        "app_key_configured": bool(creds.app_key),
        "password_configured": bool(creds.password),
        "version": creds.version,
        "style": creds.style,
        "wsdl_url": RR_WSDL_URL,
        "zeep": zeep_status(),
        "notes": [
            "Uses the official RadioReference SOAP2 API; no website scraping.",
            "RadioReference API access requires an application key and user credentials; many database calls require a Premium subscription.",
        ],
    }


def _client():
    try:
        from zeep import Client  # type: ignore
        from zeep.transports import Transport  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on Pi environment
        raise RadioReferenceError("python3-zeep is required on the Pi. Run the V0.4D deploy helper or install python3-zeep.") from exc
    return Client(wsdl=RR_WSDL_URL, transport=Transport(timeout=25, operation_timeout=45))


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if hasattr(value, "__values__"):
        return {str(k): _plain(v) for k, v in value.__values__.items()}
    if hasattr(value, "__dict__"):
        return {str(k): _plain(v) for k, v in value.__dict__.items() if not str(k).startswith("_")}
    return str(value)


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    value = _plain(value)
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _iter_values(value: Any) -> Iterable[Any]:
    value = _plain(value)
    if isinstance(value, dict):
        for v in value.values():
            yield v
            yield from _iter_values(v)
    elif isinstance(value, list):
        for item in value:
            yield item
            yield from _iter_values(item)


def _method_names(client: Any) -> list[str]:
    names: set[str] = set()
    try:
        for service in client.wsdl.services.values():
            for port in service.ports.values():
                names.update(port.binding._operations.keys())
    except Exception:
        pass
    return sorted(names)


def _call_variants(client: Any, method_name: str, variants: list[tuple[Any, ...]]) -> Any:
    service = client.service
    if not hasattr(service, method_name):
        raise RadioReferenceError(f"RadioReference method not available in WSDL: {method_name}")
    method = getattr(service, method_name)
    last_error: Exception | None = None
    for args in variants:
        try:
            return method(*args)
        except Exception as exc:
            last_error = exc
            continue
    raise RadioReferenceError(f"RadioReference {method_name} call failed: {type(last_error).__name__}: {last_error}")


def _number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _freq_to_hz(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.lower().replace("mhz", "").replace("hz", "").replace(",", "").strip()
    try:
        numeric = float(text)
    except ValueError:
        return None
    if numeric <= 0:
        return None
    if numeric < 10000:
        return int(round(numeric * 1_000_000))
    return int(round(numeric))


def _looks_control_channel(item: dict[str, Any]) -> bool:
    combined = _normalize(" ".join(f"{k} {v}" for k, v in item.items()))
    return any(token in combined for token in ("control", "primary", "alternate", "cc", "tdma cc"))


def _extract_frequencies(value: Any, prefer_control: bool = True) -> list[int]:
    frequencies: list[int] = []
    control_frequencies: list[int] = []
    for item in _iter_dicts(value):
        for key, raw in item.items():
            key_norm = _normalize(key)
            if key_norm in {"freq", "frequency", "freq mhz", "frequency mhz", "out", "out freq"} or "freq" in key_norm:
                hz = _freq_to_hz(raw)
                if hz is not None and 20_000_000 <= hz <= 1_500_000_000:
                    frequencies.append(hz)
                    if _looks_control_channel(item):
                        control_frequencies.append(hz)
    chosen = control_frequencies if prefer_control and control_frequencies else frequencies
    deduped: list[int] = []
    for hz in chosen:
        if hz not in deduped:
            deduped.append(hz)
    return deduped


def _category_from_text(*values: Any) -> str:
    text = _normalize(" ".join(str(v or "") for v in values))
    for key, category in CATEGORY_SYNONYMS.items():
        if key in text:
            return category
    return "Other"


def _extract_talkgroups(value: Any, selected_categories: list[str] | None = None, include_encrypted: bool = False) -> list[dict[str, Any]]:
    selected = set(selected_categories or [])
    talkgroups: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in _iter_dicts(value):
        keys = {str(k).lower(): k for k in item.keys()}
        raw_tgid = None
        for candidate in ("tgid", "tg_id", "decimal", "dec", "talkgroup", "talkgroupid", "tg"):
            if candidate in keys:
                raw_tgid = item[keys[candidate]]
                break
        tgid = _number(raw_tgid)
        if tgid is None or tgid <= 0 or tgid in seen:
            continue
        enc = _number(item.get("enc") or item.get("encrypted"))
        if enc == 2 and not include_encrypted:
            continue
        label = _text(
            item.get("alphaTag")
            or item.get("alpha_tag")
            or item.get("descr")
            or item.get("description")
            or item.get("label")
            or item.get("tag")
            or tgid
        )
        category = _category_from_text(item.get("tag"), item.get("descr"), item.get("description"), item.get("category"), label)
        if selected and category not in selected:
            continue
        talkgroups.append({"tgid": tgid, "label": label, "category": category, "enabled": True, "encrypted": enc})
        seen.add(tgid)
    talkgroups.sort(key=lambda tg: int(tg["tgid"]))
    return talkgroups


def test_login() -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    client = _client()
    auth = creds.auth_info()
    response = _call_variants(client, "getUserData", [(auth,), ({"authInfo": auth},)])
    return {"ok": True, "configured": True, "user_data": _plain(response), "methods": _method_names(client)}


def _find_entity_id(value: Any, wanted: str, id_keys: tuple[str, ...], text_keys: tuple[str, ...]) -> Any | None:
    wanted_norm = _normalize(wanted)
    if not wanted_norm:
        return None
    for item in _iter_dicts(value):
        text_blob = " ".join(_text(item.get(key)) for key in text_keys if key in item)
        if wanted_norm and wanted_norm in _normalize(text_blob):
            for key in id_keys:
                if key in item and item.get(key) not in (None, ""):
                    return item.get(key)
    return None


def _discover_trs_candidates(client: Any, auth: dict[str, str], state: str, county: str, city: str) -> list[dict[str, Any]]:
    """Best-effort RadioReference location discovery.

    RadioReference's WSDL is the source of truth. This routine deliberately tries
    common SOAP2 call shapes at runtime rather than hard-coding website scraping.
    """

    candidates: list[dict[str, Any]] = []
    country_info = None
    for method_name in ("getCountryInfo",):
        try:
            country_info = _call_variants(client, method_name, [("US", auth), (auth,), ({"countryCode": "US", "authInfo": auth},)])
            break
        except RadioReferenceError:
            continue
    state_id = _find_entity_id(country_info, state, ("stid", "stateId", "state_id", "id"), ("stateName", "name", "state", "stateCode", "code", "abbr")) if country_info is not None else None
    state_info = None
    if state_id is not None:
        try:
            state_info = _call_variants(client, "getStateInfo", [(state_id, auth), (auth, state_id), ({"stid": state_id, "authInfo": auth},)])
        except RadioReferenceError:
            state_info = None
    county_id = _find_entity_id(state_info, county, ("ctid", "countyId", "county_id", "id"), ("countyName", "name", "county")) if state_info is not None else None
    county_info = None
    if county_id is not None:
        try:
            county_info = _call_variants(client, "getCountyInfo", [(county_id, auth), (auth, county_id), ({"ctid": county_id, "authInfo": auth},)])
        except RadioReferenceError:
            county_info = None
    source = county_info or state_info or country_info
    city_norm = _normalize(city)
    county_norm = _normalize(county)
    for item in _iter_dicts(source):
        sid = item.get("sid") or item.get("trsId") or item.get("systemId") or item.get("id")
        if _number(sid) is None:
            continue
        name = _text(item.get("sName") or item.get("sysName") or item.get("name") or item.get("descr") or item.get("description"))
        site = _text(item.get("site") or item.get("siteName") or item.get("name"))
        blob = _normalize(" ".join(str(v or "") for v in item.values()))
        if city_norm and city_norm not in blob and city_norm not in _normalize(name) and city_norm not in _normalize(site):
            # Keep statewide/countywide candidates when county matches, because
            # RadioReference often records systems at county/state scope.
            if county_norm and county_norm not in blob:
                continue
        candidates.append({
            "system_id": int(sid),
            "name": name or f"RadioReference system {sid}",
            "site": site,
            "raw": item,
        })
    deduped: dict[int, dict[str, Any]] = {}
    for item in candidates:
        deduped.setdefault(int(item["system_id"]), item)
    return list(deduped.values())[:25]


def import_trunked_system(payload: dict[str, Any]) -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    client = _client()
    auth = creds.auth_info()
    selected_categories = [str(v) for v in payload.get("categories", []) if str(v).strip()]
    include_encrypted = bool(payload.get("include_encrypted", False))
    state = _text(payload.get("state"))
    county = _text(payload.get("county"))
    city = _text(payload.get("city"))
    system_id = _number(payload.get("system_id") or payload.get("sid"))
    site_id = _number(payload.get("site_id") or payload.get("siteId"))

    if system_id is None:
        matches = _discover_trs_candidates(client, auth, state, county, city)
        if len(matches) != 1:
            return {
                "ok": False,
                "needs_selection": True,
                "message": "RadioReference returned zero or multiple trunked system candidates; select or enter the RR System ID and import again.",
                "matches": matches,
                "searched": {"state": state, "county": county, "city": city},
                "available_methods": _method_names(client),
            }
        system_id = int(matches[0]["system_id"])

    details = _call_variants(client, "getTrsDetails", [(system_id, auth), (auth, system_id), ({"sid": system_id, "authInfo": auth},)])
    details_plain = _plain(details)
    sites_plain: Any = None
    try:
        sites_plain = _plain(_call_variants(client, "getTrsSites", [(system_id, auth), (auth, system_id), ({"sid": system_id, "authInfo": auth},)]))
    except RadioReferenceError:
        sites_plain = None

    site_candidates = list(_iter_dicts(sites_plain or details_plain))
    if site_id is not None:
        filtered_sites = []
        for item in site_candidates:
            sid_value = _number(item.get("siteId") or item.get("site_id") or item.get("siteNumber") or item.get("sid") or item.get("id"))
            if sid_value == site_id:
                filtered_sites.append(item)
        if filtered_sites:
            site_candidates = filtered_sites

    control_channels = _extract_frequencies(site_candidates, prefer_control=True) or _extract_frequencies(details_plain, prefer_control=True)
    talkgroups = _extract_talkgroups(details_plain, selected_categories, include_encrypted)
    if not control_channels:
        raise RadioReferenceError("RadioReference import did not find control/site frequencies for that system. Try entering the exact RR Site ID or inspect the returned raw details.")
    if not talkgroups:
        raise RadioReferenceError("RadioReference import did not find matching talkgroups. Try selecting more categories or allow encrypted/mixed groups if appropriate.")

    system_name = _text(payload.get("name"))
    if not system_name:
        for item in _iter_dicts(details_plain):
            system_name = _text(item.get("sName") or item.get("sysName") or item.get("name") or item.get("descr"))
            if system_name:
                break
    if not system_name:
        system_name = f"RadioReference System {system_id}"
    site_name = _text(payload.get("site")) or city or county or "RadioReference Import"

    config = {
        "schema_version": 1,
        "systems": [
            {
                "name": system_name,
                "enabled": True,
                "mode": "p25_trunked",
                "site": site_name,
                "control_channels_hz": control_channels,
                "voice_channels_hz": [],
                "talkgroups": [
                    {"tgid": int(tg["tgid"]), "label": str(tg["label"]), "enabled": True, "category": tg.get("category", "Other")}
                    for tg in talkgroups
                ],
                "receiver_roles": {
                    "p25_control": {"rtl_serial": str(payload.get("control_serial") or "00000162"), "gain_db": float(payload.get("gain_db") or 40.2), "ppm": int(payload.get("ppm") or 0)},
                    "p25_voice": {"rtl_serial": str(payload.get("voice_serial") or "00000251"), "gain_db": float(payload.get("gain_db") or 40.2), "ppm": int(payload.get("ppm") or 0)},
                },
                "decoder": {"engine": "op25", "phase_ii_enabled": True, "mute_encrypted": True},
                "source": {"type": "radioreference_soap", "system_id": system_id, "site_id": site_id, "imported_utc": time.time()},
            }
        ],
    }
    return {
        "ok": True,
        "source": "radioreference_soap",
        "system_id": system_id,
        "site_id": site_id,
        "control_channel_count": len(control_channels),
        "talkgroup_count": len(talkgroups),
        "selected_categories": selected_categories,
        "config": config,
        "raw_summary": {
            "details_keys": sorted(details_plain.keys()) if isinstance(details_plain, dict) else [],
            "site_object_count": len(site_candidates),
        },
    }
