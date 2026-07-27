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




def zeep_status() -> dict[str, Any]:
    try:
        import zeep  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on Pi environment
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    version = getattr(zeep, "__version__", "unknown")
    return {"available": True, "version": version}


def _radioreference_status_base() -> dict[str, Any]:
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


def _plain_v1(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain_v1(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain_v1(v) for v in value]
    if hasattr(value, "__values__"):
        return {str(k): _plain_v1(v) for k, v in value.__values__.items()}
    if hasattr(value, "__dict__"):
        return {str(k): _plain_v1(v) for k, v in value.__dict__.items() if not str(k).startswith("_")}
    return str(value)


def _iter_dicts_v1(value: Any) -> Iterable[dict[str, Any]]:
    value = _plain_v1(value)
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts_v1(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts_v1(item)


def _iter_values_v1(value: Any) -> Iterable[Any]:
    value = _plain_v1(value)
    if isinstance(value, dict):
        for v in value.values():
            yield v
            yield from _iter_values_v1(v)
    elif isinstance(value, list):
        for item in value:
            yield item
            yield from _iter_values_v1(item)


def _method_names(client: Any) -> list[str]:
    names: set[str] = set()
    try:
        for service in client.wsdl.services.values():
            for port in service.ports.values():
                names.update(port.binding._operations.keys())
    except Exception:
        pass
    return sorted(names)


def _call_variants_v1(client: Any, method_name: str, variants: list[tuple[Any, ...]]) -> Any:
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
    for item in _iter_dicts_v1(value):
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
    for item in _iter_dicts_v1(value):
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




def _find_entity_id(value: Any, wanted: str, id_keys: tuple[str, ...], text_keys: tuple[str, ...]) -> Any | None:
    wanted_norm = _normalize(wanted)
    if not wanted_norm:
        return None
    for item in _iter_dicts_v1(value):
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
            country_info = _call_variants_v1(client, method_name, [("US", auth), (auth,), ({"countryCode": "US", "authInfo": auth},)])
            break
        except RadioReferenceError:
            continue
    state_id = _find_entity_id(country_info, state, ("stid", "stateId", "state_id", "id"), ("stateName", "name", "state", "stateCode", "code", "abbr")) if country_info is not None else None
    state_info = None
    if state_id is not None:
        try:
            state_info = _call_variants_v1(client, "getStateInfo", [(state_id, auth), (auth, state_id), ({"stid": state_id, "authInfo": auth},)])
        except RadioReferenceError:
            state_info = None
    county_id = _find_entity_id(state_info, county, ("ctid", "countyId", "county_id", "id"), ("countyName", "name", "county")) if state_info is not None else None
    county_info = None
    if county_id is not None:
        try:
            county_info = _call_variants_v1(client, "getCountyInfo", [(county_id, auth), (auth, county_id), ({"ctid": county_id, "authInfo": auth},)])
        except RadioReferenceError:
            county_info = None
    source = county_info or state_info or country_info
    city_norm = _normalize(city)
    county_norm = _normalize(county)
    for item in _iter_dicts_v1(source):
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



# BEGIN V0.4D2 RadioReference credential preserve and SOAP auth fix
# This compatibility block deliberately overrides a few earlier functions at
# module-load time. It keeps existing Pi-local secrets when the browser sends
# blank password/API-key fields, and it tries the RadioReference SOAP authInfo
# argument as a named argument before falling back to older positional shapes.
_RR_V0_4D2_SECRET_PLACEHOLDERS = {
    "",
    "********",
    "<hidden>",
    "hidden",
    "saved",
    "saved on pi",
    "leave blank to keep",
    "saved on pi; leave blank to keep",
}


def _rr_v0_4d2_payload_value(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if key in payload:
            value = payload.get(key)
            if value is None:
                return ""
            return str(value)
    return None


def _rr_v0_4d2_secret_value(payload: dict[str, Any], existing: str, *keys: str, strip: bool = True) -> str:
    raw = _rr_v0_4d2_payload_value(payload, *keys)
    if raw is None:
        return existing
    value = raw.strip() if strip else raw
    if value.strip().lower() in _RR_V0_4D2_SECRET_PLACEHOLDERS:
        return existing
    return value


def _rr_v0_4d2_write_credentials(creds: RadioReferenceCredentials) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        [
            "# Local RadioReference credentials for PI-P25-SCANNER.",
            "# This file must stay local on the Pi and must not be committed.",
            f"RADIOREFERENCE_APP_KEY={_quote_env(creds.app_key)}",
            f"RADIOREFERENCE_USERNAME={_quote_env(creds.username)}",
            f"RADIOREFERENCE_PASSWORD={_quote_env(creds.password)}",
            f"RADIOREFERENCE_VERSION={_quote_env(creds.version or RR_VERSION)}",
            f"RADIOREFERENCE_STYLE={_quote_env(creds.style or RR_STYLE)}",
            "",
        ]
    )
    RR_ENV_PATH.write_text(text, encoding="utf-8")
    try:
        RR_ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


_rr_v0_4d2_base_status = _radioreference_status_base


def radioreference_status() -> dict[str, Any]:
    payload = _rr_v0_4d2_base_status()
    payload["credential_save_mode"] = "preserve-existing-secrets-v0.4d2"
    payload["soap_auth_mode"] = "named-authInfo-first-v0.4d2"
    return payload


def save_credentials(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    existing = load_credentials()
    app_key = _rr_v0_4d2_secret_value(
        payload,
        existing.app_key,
        "app_key",
        "appKey",
        "api_key",
        "apiKey",
        "RADIOREFERENCE_APP_KEY",
        "RR_APP_KEY",
    ).strip()
    username = _rr_v0_4d2_secret_value(
        payload,
        existing.username,
        "username",
        "userName",
        "RADIOREFERENCE_USERNAME",
        "RR_USERNAME",
    ).strip()
    password = _rr_v0_4d2_secret_value(
        payload,
        existing.password,
        "password",
        "RADIOREFERENCE_PASSWORD",
        "RR_PASSWORD",
        strip=False,
    )
    version = str(payload.get("version") or existing.version or RR_VERSION).strip() or RR_VERSION
    style = str(payload.get("style") or existing.style or RR_STYLE).strip() or RR_STYLE
    missing = []
    if not app_key:
        missing.append("API key")
    if not username:
        missing.append("username")
    if not password:
        missing.append("password")
    if missing:
        raise RadioReferenceError("RadioReference credentials missing after save: " + ", ".join(missing))
    _rr_v0_4d2_write_credentials(
        RadioReferenceCredentials(
            app_key=app_key,
            username=username,
            password=password,
            version=version,
            style=style,
        )
    )
    return radioreference_status()


def _rr_v0_4d2_call_variant(method: Any, variant: Any) -> Any:
    if isinstance(variant, tuple) and len(variant) == 2 and variant[0] == "__kwargs__" and isinstance(variant[1], dict):
        return method(**variant[1])
    if isinstance(variant, dict) and "__kwargs__" in variant and isinstance(variant["__kwargs__"], dict):
        return method(**variant["__kwargs__"])
    if isinstance(variant, tuple):
        return method(*variant)
    return method(variant)


def _call_variants_v2(client: Any, method_name: str, variants: list[Any]) -> Any:
    service = client.service
    if not hasattr(service, method_name):
        raise RadioReferenceError(f"RadioReference method not available in WSDL: {method_name}")
    method = getattr(service, method_name)
    errors: list[str] = []
    for variant in variants:
        try:
            return _rr_v0_4d2_call_variant(method, variant)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
    joined = " | ".join(errors[-4:]) if errors else "no attempted call shapes"
    raise RadioReferenceError(f"RadioReference {method_name} call failed after {len(variants)} call shapes: {joined}")


def _rr_v0_4d2_auth_variants(auth: dict[str, str]) -> list[Any]:
    # Preferred zeep shape for the RadioReference SOAP2 WSDL is a named authInfo
    # argument. The positional variants remain for compatibility with older or
    # alternate WSDL bindings.
    return [
        ("__kwargs__", {"authInfo": auth}),
        (auth,),
        ({"authInfo": auth},),
    ]


def test_login() -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        missing = []
        if not creds.app_key:
            missing.append("API key")
        if not creds.username:
            missing.append("username")
        if not creds.password:
            missing.append("password")
        raise RadioReferenceError("RadioReference credentials are not configured: missing " + ", ".join(missing))
    client = _client()
    auth = creds.auth_info()
    response = _call_variants_v2(client, "getUserData", _rr_v0_4d2_auth_variants(auth))
    return {
        "ok": True,
        "configured": True,
        "username": creds.username,
        "auth_mode": "named-authInfo-first-v0.4d2",
        "user_data": _plain_v1(response),
        "methods": _method_names(client),
    }
# END V0.4D2 RadioReference credential preserve and SOAP auth fix

# BEGIN V0.4D3 RadioReference system/site picker
# Runtime extension: discover human-readable trunked systems and sites from the
# official RadioReference SOAP data. These helpers intentionally use flexible,
# case-insensitive extraction because the SOAP object names vary by endpoint.

def _rr_ci_value(item: dict[str, Any], *names: str) -> Any:
    if not isinstance(item, dict):
        return None
    wanted = {_normalize(name) for name in names if str(name or "").strip()}
    for key, value in item.items():
        if _normalize(key) in wanted:
            return value
    # A few RadioReference keys vary only by punctuation/case; allow substring
    # matches after exact normalized matching.
    for key, value in item.items():
        key_norm = _normalize(key)
        if any(name and (key_norm == name or key_norm.endswith(" " + name) or name.endswith(" " + key_norm)) for name in wanted):
            return value
    return None


def _rr_first_text(item: dict[str, Any], *names: str) -> str:
    for name in names:
        value = _rr_ci_value(item, name)
        text = _text(value)
        if text:
            return text
    return ""


def _rr_first_number(item: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = _rr_ci_value(item, name)
        number = _number(value)
        if number is not None:
            return number
    return None


def _rr_blob(item: dict[str, Any]) -> str:
    try:
        return _normalize(" ".join([*(str(k) for k in item.keys()), *(str(v) for v in item.values())]))
    except Exception:
        return _normalize(str(item))


def _rr_candidate_score(item: dict[str, Any], state: str, county: str, city: str) -> int:
    blob = _rr_blob(item)
    score = 0
    for value, weight in ((city, 30), (county, 20), (state, 10)):
        norm = _normalize(value)
        if norm and norm in blob:
            score += weight
    if "trunk" in blob or "trs" in blob:
        score += 15
    if "p25" in blob or "project 25" in blob:
        score += 5
    return score


def _rr_extract_system_candidates(value: Any, state: str = "", county: str = "", city: str = "") -> list[dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    for item in _iter_dicts_v1(value):
        if not isinstance(item, dict):
            continue
        sid = _rr_first_number(
            item,
            "trsid", "trs id", "trsId", "trs_id", "system id", "systemId", "system_id", "sid",
        )
        if sid is None or sid <= 0:
            continue
        name = _rr_first_text(
            item,
            "sName", "sname", "sysName", "systemName", "system name", "name", "descr", "description",
        )
        site = _rr_first_text(item, "siteName", "site name", "site", "zone", "rfss")
        system_type = _rr_first_text(item, "type", "systemType", "system type", "flavor", "voice")
        county_name = _rr_first_text(item, "countyName", "county name", "county")
        state_name = _rr_first_text(item, "stateName", "state name", "state", "stateCode", "state code")
        blob = _rr_blob(item)
        # Avoid treating ordinary state/county objects as systems. A candidate
        # needs a system-ish id/name key or trunked-system words in the object.
        key_blob = _normalize(" ".join(str(k) for k in item.keys()))
        if not ("trs" in key_blob or "system" in key_blob or "sname" in key_blob or "trunk" in blob or "p25" in blob):
            if not name:
                continue
        if not name:
            name = f"RadioReference System {sid}"
        score = _rr_candidate_score(item, state, county, city)
        existing = candidates.get(sid)
        candidate = {
            "system_id": int(sid),
            "name": name,
            "site": site,
            "county": county_name,
            "state": state_name,
            "type": system_type,
            "score": score,
            "display_name": " — ".join(part for part in [name, site, county_name or state_name, f"RR {sid}"] if part),
            "raw": item,
        }
        if existing is None or score > int(existing.get("score") or 0):
            candidates[sid] = candidate
    ordered = sorted(candidates.values(), key=lambda row: (-int(row.get("score") or 0), str(row.get("name") or ""), int(row.get("system_id") or 0)))
    return ordered[:100]


def _rr_discovery_sources(client: Any, auth: dict[str, str], state: str, county: str, city: str) -> tuple[list[Any], dict[str, Any]]:
    sources: list[Any] = []
    debug: dict[str, Any] = {"calls": [], "errors": []}
    country_info = None
    try:
        country_info = _call_variants_v2(client, "getCountryInfo", [("US", auth), (auth,), ({"countryCode": "US", "authInfo": auth},)])
        sources.append(country_info)
        debug["calls"].append("getCountryInfo")
    except Exception as exc:
        debug["errors"].append(f"getCountryInfo: {type(exc).__name__}: {exc}")

    state_id = _find_entity_id(country_info, state, ("stid", "stateId", "state_id", "id"), ("stateName", "name", "state", "stateCode", "code", "abbr")) if country_info is not None else None
    state_info = None
    if state_id is not None:
        try:
            state_info = _call_variants_v2(client, "getStateInfo", [(state_id, auth), (auth, state_id), ({"stid": state_id, "authInfo": auth},)])
            sources.append(state_info)
            debug["calls"].append(f"getStateInfo:{state_id}")
        except Exception as exc:
            debug["errors"].append(f"getStateInfo: {type(exc).__name__}: {exc}")

    county_id = _find_entity_id(state_info, county, ("ctid", "countyId", "county_id", "id"), ("countyName", "name", "county")) if state_info is not None else None
    county_info = None
    if county_id is not None:
        try:
            county_info = _call_variants_v2(client, "getCountyInfo", [(county_id, auth), (auth, county_id), ({"ctid": county_id, "authInfo": auth},)])
            sources.append(county_info)
            debug["calls"].append(f"getCountyInfo:{county_id}")
        except Exception as exc:
            debug["errors"].append(f"getCountyInfo: {type(exc).__name__}: {exc}")

    debug["state_id"] = state_id
    debug["county_id"] = county_id
    return sources, debug


def _discover_trs_candidates(client: Any, auth: dict[str, str], state: str, county: str, city: str) -> list[dict[str, Any]]:
    # Overrides the earlier V0.4D implementation with a less brittle,
    # case-insensitive collector. Prefer county/state candidates but keep a
    # broader fallback so the UI can show the user a selectable list.
    sources, _debug = _rr_discovery_sources(client, auth, state, county, city)
    county_city_matches: list[dict[str, Any]] = []
    broad_matches: list[dict[str, Any]] = []
    for source in sources:
        broad_matches.extend(_rr_extract_system_candidates(source, state=state, county=county, city=city))
    if city or county:
        city_norm = _normalize(city)
        county_norm = _normalize(county)
        for row in broad_matches:
            blob = _normalize(json.dumps(row.get("raw", {}), default=str))
            if (city_norm and city_norm in blob) or (county_norm and county_norm in blob):
                county_city_matches.append(row)
    chosen = county_city_matches or broad_matches
    deduped: dict[int, dict[str, Any]] = {}
    for row in chosen:
        deduped.setdefault(int(row["system_id"]), row)
    return list(deduped.values())[:25]


def discover_trunked_systems(payload: dict[str, Any]) -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    client = _client()
    auth = creds.auth_info()
    state = _text(payload.get("state"))
    county = _text(payload.get("county"))
    city = _text(payload.get("city"))
    sources, debug = _rr_discovery_sources(client, auth, state, county, city)
    systems: list[dict[str, Any]] = []
    for source in sources:
        systems.extend(_rr_extract_system_candidates(source, state=state, county=county, city=city))
    deduped: dict[int, dict[str, Any]] = {}
    for row in systems:
        sid = int(row["system_id"])
        old = deduped.get(sid)
        if old is None or int(row.get("score") or 0) > int(old.get("score") or 0):
            deduped[sid] = row
    final = sorted(deduped.values(), key=lambda row: (-int(row.get("score") or 0), str(row.get("name") or ""), int(row.get("system_id") or 0)))[:100]
    return {
        "ok": True,
        "systems": final,
        "count": len(final),
        "searched": {"state": state, "county": county, "city": city},
        "debug": debug,
        "available_methods": _method_names(client),
    }


def _rr_extract_sites(value: Any) -> list[dict[str, Any]]:
    sites: dict[int, dict[str, Any]] = {}
    fallback_index = 1
    for item in _iter_dicts_v1(value):
        if not isinstance(item, dict):
            continue
        blob = _rr_blob(item)
        key_blob = _normalize(" ".join(str(k) for k in item.keys()))
        has_freq = any("freq" in _normalize(k) for k in item.keys())
        if not ("site" in key_blob or "rfss" in key_blob or has_freq or "control" in blob):
            continue
        site_id = _rr_first_number(item, "siteId", "site id", "site_id", "siteNumber", "site number", "site", "id")
        if site_id is None:
            site_id = fallback_index
            fallback_index += 1
        if site_id <= 0:
            continue
        name = _rr_first_text(item, "siteName", "site name", "name", "descr", "description", "zone", "rfss")
        county = _rr_first_text(item, "countyName", "county name", "county")
        state_name = _rr_first_text(item, "stateName", "state name", "state", "stateCode")
        freqs = _extract_frequencies([item], prefer_control=False)
        control_freqs = _extract_frequencies([item], prefer_control=True)
        if not name:
            name = f"Site {site_id}"
        row = {
            "site_id": int(site_id),
            "name": name,
            "county": county,
            "state": state_name,
            "frequency_count": len(freqs),
            "frequencies_hz": freqs[:50],
            "control_channels_hz": control_freqs[:20],
            "display_name": " — ".join(part for part in [name, county or state_name, f"Site {site_id}"] if part),
            "raw": item,
        }
        old = sites.get(int(site_id))
        if old is None or len(row["frequencies_hz"]) > len(old.get("frequencies_hz") or []):
            sites[int(site_id)] = row
    return sorted(sites.values(), key=lambda row: (str(row.get("name") or ""), int(row.get("site_id") or 0)))[:200]


def discover_trunked_sites(payload: dict[str, Any]) -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    system_id = _number(payload.get("system_id") or payload.get("sid"))
    if system_id is None:
        raise RadioReferenceError("RR System ID is required before loading sites")
    client = _client()
    auth = creds.auth_info()
    sources: list[Any] = []
    errors: list[str] = []
    try:
        sources.append(_call_variants_v2(client, "getTrsSites", [(system_id, auth), (auth, system_id), ({"sid": system_id, "authInfo": auth},)]))
    except Exception as exc:
        errors.append(f"getTrsSites: {type(exc).__name__}: {exc}")
    try:
        sources.append(_call_variants_v2(client, "getTrsDetails", [(system_id, auth), (auth, system_id), ({"sid": system_id, "authInfo": auth},)]))
    except Exception as exc:
        errors.append(f"getTrsDetails: {type(exc).__name__}: {exc}")
    sites: list[dict[str, Any]] = []
    for source in sources:
        sites.extend(_rr_extract_sites(source))
    deduped: dict[int, dict[str, Any]] = {}
    for row in sites:
        sid = int(row["site_id"])
        old = deduped.get(sid)
        if old is None or int(row.get("frequency_count") or 0) > int(old.get("frequency_count") or 0):
            deduped[sid] = row
    final = sorted(deduped.values(), key=lambda row: (str(row.get("name") or ""), int(row.get("site_id") or 0)))
    return {"ok": True, "system_id": system_id, "sites": final, "count": len(final), "errors": errors, "available_methods": _method_names(client)}
# END V0.4D3 RadioReference system/site picker

# BEGIN RR_PICKER_DISCOVERY_V0_4D3B
def _rr_picker_call(client: Any, method_name: str, variants: list[Any], *, required: bool = False) -> Any | None:
    """Call a RadioReference SOAP method using several observed SOAP2 shapes."""

    service = client.service
    if not hasattr(service, method_name):
        if required:
            raise RadioReferenceError(f"RadioReference method not available in WSDL: {method_name}")
        return None
    method = getattr(service, method_name)
    last_error: Exception | None = None
    for variant in variants:
        try:
            if isinstance(variant, dict) and "__kwargs__" in variant:
                return method(**variant["__kwargs__"])
            if isinstance(variant, dict) and "__args__" in variant:
                return method(*variant["__args__"])
            if isinstance(variant, tuple):
                return method(*variant)
            if isinstance(variant, dict):
                try:
                    return method(**variant)
                except Exception:
                    return method(variant)
            return method(variant)
        except Exception as exc:
            last_error = exc
            continue
    if required:
        raise RadioReferenceError(f"RadioReference {method_name} call failed: {type(last_error).__name__}: {last_error}")
    return None


def _rr_picker_find_id(value: Any, wanted: str, id_keys: tuple[str, ...], text_keys: tuple[str, ...]) -> Any | None:
    wanted_norms = {_normalize(wanted)}
    wanted_norms.update({_normalize(part) for part in str(wanted or "").replace(",", " ").split() if part.strip()})
    wanted_norms.discard("")
    if not wanted_norms:
        return None
    for item in _iter_dicts_v1(value):
        text_blob = " ".join(_text(item.get(key)) for key in text_keys if key in item)
        blob_norm = _normalize(text_blob)
        if any(w == blob_norm or w in blob_norm or blob_norm in w for w in wanted_norms):
            for key in id_keys:
                if key in item and item.get(key) not in (None, ""):
                    return item.get(key)
    return None


def _rr_picker_system_id(item: dict[str, Any]) -> int | None:
    for key in ("sid", "systemId", "system_id", "trsId", "trs_id", "sysid", "sysId", "id"):
        if key in item:
            sid = _number(item.get(key))
            if sid is not None and sid > 0:
                return sid
    return None


def _rr_picker_system_name(item: dict[str, Any]) -> str:
    return _text(
        item.get("sName")
        or item.get("sysName")
        or item.get("systemName")
        or item.get("trsName")
        or item.get("name")
        or item.get("descr")
        or item.get("description")
        or item.get("label")
    )


def _rr_picker_extract_systems(value: Any, *, source: str, searched: dict[str, str]) -> list[dict[str, Any]]:
    systems: list[dict[str, Any]] = []
    city_norm = _normalize(searched.get("city"))
    county_norm = _normalize(searched.get("county"))
    state_norm = _normalize(searched.get("state"))
    for item in _iter_dicts_v1(value):
        sid = _rr_picker_system_id(item)
        if sid is None:
            continue
        name = _rr_picker_system_name(item)
        if not name:
            continue
        blob = _normalize(" ".join(str(v or "") for v in item.values()))
        rank = 50
        if city_norm and city_norm in blob:
            rank = min(rank, 0)
        if county_norm and county_norm in blob:
            rank = min(rank, 10)
        if state_norm and state_norm in blob:
            rank = min(rank, 20)
        systems.append(
            {
                "system_id": int(sid),
                "name": name,
                "site": _text(item.get("site") or item.get("siteName") or item.get("zone") or item.get("region")),
                "county": _text(item.get("county") or item.get("countyName") or searched.get("county")),
                "state": _text(item.get("state") or item.get("stateCode") or item.get("st") or searched.get("state")),
                "description": _text(item.get("descr") or item.get("description") or item.get("notes")),
                "source": source,
                "rank": rank,
                "raw_keys": sorted(str(k) for k in item.keys()),
            }
        )
    return systems




def _rr_picker_site_id(item: dict[str, Any]) -> int | None:
    for key in ("siteId", "site_id", "siteNumber", "siteNo", "site", "sid", "id"):
        if key in item:
            value = _number(item.get(key))
            if value is not None and value >= 0:
                return value
    return None


def _rr_picker_site_name(item: dict[str, Any]) -> str:
    return _text(
        item.get("siteName")
        or item.get("site_name")
        or item.get("name")
        or item.get("descr")
        or item.get("description")
        or item.get("zone")
        or item.get("rfss")
    )


# END RR_PICKER_DISCOVERY_V0_4D3B

# BEGIN RR_PICKER_STATE_COUNTY_FIX_V0_4D3C
_US_STATE_ALIASES_V0_4D3C = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def _rr_d3c_state_tokens(value: Any) -> set[str]:
    text = _normalize(value)
    tokens = {text} if text else set()
    if len(str(value or "").strip()) == 2:
        tokens.add(str(value).strip().upper().lower())
    abbr = _US_STATE_ALIASES_V0_4D3C.get(text)
    if abbr:
        tokens.add(abbr.lower())
    for name, code in _US_STATE_ALIASES_V0_4D3C.items():
        if text == code.lower():
            tokens.add(name)
            tokens.add(code.lower())
    return {t for t in tokens if t}


def _rr_d3c_call(client: Any, method_name: str, variants: list[Any], *, collect_errors: list[dict[str, str]] | None = None) -> Any | None:
    service = client.service
    if not hasattr(service, method_name):
        if collect_errors is not None:
            collect_errors.append({"method": method_name, "error": "method not available"})
        return None
    method = getattr(service, method_name)
    last_error: Exception | None = None
    for index, variant in enumerate(variants):
        try:
            if isinstance(variant, dict) and "__kwargs__" in variant:
                return method(**variant["__kwargs__"])
            if isinstance(variant, dict) and "__args__" in variant:
                return method(*variant["__args__"])
            if isinstance(variant, tuple):
                return method(*variant)
            if isinstance(variant, dict):
                try:
                    return method(**variant)
                except Exception:
                    return method(variant)
            return method(variant)
        except Exception as exc:
            last_error = exc
            continue
    if collect_errors is not None:
        collect_errors.append({"method": method_name, "error": f"{type(last_error).__name__}: {last_error}"})
    return None


def _rr_d3c_find_state_id(value: Any, wanted_state: str) -> Any | None:
    wanted = _rr_d3c_state_tokens(wanted_state)
    if not wanted:
        return None
    id_keys = ("stid", "stateId", "state_id", "stateID", "id")
    text_keys = ("stateCode", "state_code", "code", "abbr", "state", "name", "stateName", "state_name", "shortName")
    for item in _iter_dicts_v1(value):
        blob_parts = []
        for key in text_keys:
            if key in item:
                blob_parts.append(_text(item.get(key)))
        blob = _normalize(" ".join(blob_parts))
        field_tokens = set()
        for part in blob_parts:
            field_tokens.update(_rr_d3c_state_tokens(part))
        if wanted.intersection(field_tokens) or any(token and token in blob for token in wanted):
            for key in id_keys:
                if key in item and item.get(key) not in (None, ""):
                    return item.get(key)
    return None


def _rr_d3c_find_county_id(value: Any, wanted_county: str) -> Any | None:
    wanted = _normalize(wanted_county).replace(" county", "").strip()
    if not wanted:
        return None
    id_keys = ("ctid", "countyId", "county_id", "countyID", "id")
    text_keys = ("countyName", "county_name", "county", "name", "descr", "description")
    for item in _iter_dicts_v1(value):
        text = _normalize(" ".join(_text(item.get(key)) for key in text_keys if key in item)).replace(" county", "").strip()
        if wanted == text or wanted in text or text in wanted:
            for key in id_keys:
                if key in item and item.get(key) not in (None, ""):
                    return item.get(key)
    return None


def _rr_d3c_source_summary(name: str, value: Any) -> dict[str, Any]:
    count = 0
    keys: set[str] = set()
    for item in _iter_dicts_v1(value):
        count += 1
        keys.update(str(k) for k in item.keys())
        if count >= 300:
            break
    return {"name": name, "dict_count_sample": count, "keys_sample": sorted(keys)[:80]}


def _rr_d3c_system_id(item: dict[str, Any]) -> int | None:
    for key in ("sid", "systemId", "system_id", "systemID", "trsId", "trsid", "trs_id", "sysid", "sysId", "id"):
        if key in item:
            value = _number(item.get(key))
            if value is not None and value > 0:
                return value
    return None


def _rr_d3c_system_name(item: dict[str, Any]) -> str:
    return _text(
        item.get("sName")
        or item.get("sysName")
        or item.get("systemName")
        or item.get("trsName")
        or item.get("name")
        or item.get("descr")
        or item.get("description")
        or item.get("label")
    )


def _rr_d3c_extract_systems(value: Any, *, source: str, searched: dict[str, str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    city_norm = _normalize(searched.get("city"))
    county_norm = _normalize(searched.get("county"))
    state_tokens = _rr_d3c_state_tokens(searched.get("state"))
    for item in _iter_dicts_v1(value):
        sid = _rr_d3c_system_id(item)
        name = _rr_d3c_system_name(item)
        if sid is None or not name:
            continue
        blob = _normalize(" ".join(str(v or "") for v in item.values()))
        rank = 60
        if city_norm and city_norm in blob:
            rank = min(rank, 0)
        if county_norm and county_norm in blob:
            rank = min(rank, 10)
        if state_tokens and any(token in blob for token in state_tokens):
            rank = min(rank, 20)
        if not city_norm and not county_norm:
            rank = min(rank, 30)
        results.append(
            {
                "system_id": int(sid),
                "name": name,
                "site": _text(item.get("site") or item.get("siteName") or item.get("zone") or item.get("region")),
                "county": _text(item.get("county") or item.get("countyName") or searched.get("county")),
                "state": _text(item.get("state") or item.get("stateCode") or item.get("st") or searched.get("state")),
                "description": _text(item.get("descr") or item.get("description") or item.get("notes")),
                "source": source,
                "rank": rank,
                "raw_keys": sorted(str(k) for k in item.keys()),
            }
        )
    return results


def _rr_d3c_states_variants(auth: dict[str, str]) -> list[Any]:
    return [
        {"__kwargs__": {"authInfo": auth}},
        {"__kwargs__": {"countryCode": "US", "authInfo": auth}},
        {"__kwargs__": {"coid": "US", "authInfo": auth}},
        (auth,),
        ("US", auth),
        (auth, "US"),
        ({"authInfo": auth},),
        ({"countryCode": "US", "authInfo": auth},),
    ]


def _rr_d3c_state_info_variants(state: str, state_id: Any, auth: dict[str, str]) -> list[Any]:
    variants: list[Any] = []
    for value in (state_id, state, str(state or "").upper()):
        if value in (None, ""):
            continue
        variants.extend([
            {"__kwargs__": {"stid": value, "authInfo": auth}},
            {"__kwargs__": {"stateId": value, "authInfo": auth}},
            {"__kwargs__": {"stateCode": value, "authInfo": auth}},
            (value, auth),
            (auth, value),
            ({"stid": value, "authInfo": auth},),
        ])
    return variants


def _rr_d3c_counties_variants(state: str, state_id: Any, auth: dict[str, str]) -> list[Any]:
    variants: list[Any] = []
    for value in (state_id, state, str(state or "").upper()):
        if value in (None, ""):
            continue
        variants.extend([
            {"__kwargs__": {"stid": value, "authInfo": auth}},
            {"__kwargs__": {"stateId": value, "authInfo": auth}},
            {"__kwargs__": {"stateCode": value, "authInfo": auth}},
            (value, auth),
            (auth, value),
            ({"stid": value, "authInfo": auth},),
        ])
    return variants


def _rr_d3c_county_info_variants(county: str, county_id: Any, state: str, state_id: Any, auth: dict[str, str]) -> list[Any]:
    variants: list[Any] = []
    for value in (county_id, county):
        if value in (None, ""):
            continue
        variants.extend([
            {"__kwargs__": {"ctid": value, "authInfo": auth}},
            {"__kwargs__": {"countyId": value, "authInfo": auth}},
            (value, auth),
            (auth, value),
            ({"ctid": value, "authInfo": auth},),
        ])
    if county and state:
        variants.extend([
            {"__kwargs__": {"county": county, "state": state, "authInfo": auth}},
            {"__kwargs__": {"countyName": county, "stateCode": state, "authInfo": auth}},
            (county, state, auth),
            (state, county, auth),
        ])
    if county and state_id not in (None, ""):
        variants.extend([
            {"__kwargs__": {"county": county, "stid": state_id, "authInfo": auth}},
            {"__kwargs__": {"countyName": county, "stateId": state_id, "authInfo": auth}},
        ])
    return variants


def discover_radioreference_systems(payload: dict[str, Any]) -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    client = _client()
    auth = creds.auth_info()
    state = _text(payload.get("state")) or _text(payload.get("state_code"))
    county = _text(payload.get("county"))
    city = _text(payload.get("city"))
    searched = {"state": state, "county": county, "city": city}
    call_errors: list[dict[str, str]] = []
    sources: list[tuple[str, Any]] = []

    states_by_list = _rr_d3c_call(client, "getStatesByList", _rr_d3c_states_variants(auth), collect_errors=call_errors)
    if states_by_list is not None:
        sources.append(("getStatesByList", states_by_list))
    country_info = _rr_d3c_call(
        client,
        "getCountryInfo",
        [
            {"__kwargs__": {"countryCode": "US", "authInfo": auth}},
            {"__kwargs__": {"country": "US", "authInfo": auth}},
            ("US", auth),
            (auth, "US"),
            ({"countryCode": "US", "authInfo": auth},),
        ],
        collect_errors=call_errors,
    )
    if country_info is not None:
        sources.append(("getCountryInfo", country_info))

    state_id = _rr_d3c_find_state_id(states_by_list, state) if states_by_list is not None else None
    if state_id is None and country_info is not None:
        state_id = _rr_d3c_find_state_id(country_info, state)

    state_info = _rr_d3c_call(client, "getStateInfo", _rr_d3c_state_info_variants(state, state_id, auth), collect_errors=call_errors)
    if state_info is not None:
        sources.append(("getStateInfo", state_info))
        if state_id is None:
            state_id = _rr_d3c_find_state_id(state_info, state)

    counties_by_list = _rr_d3c_call(client, "getCountiesByList", _rr_d3c_counties_variants(state, state_id, auth), collect_errors=call_errors)
    if counties_by_list is not None:
        sources.append(("getCountiesByList", counties_by_list))

    county_id = _rr_d3c_find_county_id(counties_by_list, county) if counties_by_list is not None else None
    if county_id is None and state_info is not None:
        county_id = _rr_d3c_find_county_id(state_info, county)

    county_info = _rr_d3c_call(client, "getCountyInfo", _rr_d3c_county_info_variants(county, county_id, state, state_id, auth), collect_errors=call_errors)
    if county_info is not None:
        sources.append(("getCountyInfo", county_info))
        if county_id is None:
            county_id = _rr_d3c_find_county_id(county_info, county)

    # WSDL variants vary by account/version. Try direct TRS list helpers if a future
    # WSDL exposes them, but do not fail when absent.
    for method_name in ("getTrsByCounty", "getTrsSystemsByCounty", "getTrsListByCounty", "getTrsByState", "getTrsSystemsByState", "getTrsListByState"):
        direct = _rr_d3c_call(
            client,
            method_name,
            _rr_d3c_county_info_variants(county, county_id, state, state_id, auth) + _rr_d3c_state_info_variants(state, state_id, auth),
            collect_errors=call_errors,
        )
        if direct is not None:
            sources.append((method_name, direct))

    systems_by_id: dict[int, dict[str, Any]] = {}
    for source, value in sources:
        for system in _rr_d3c_extract_systems(value, source=source, searched=searched):
            existing = systems_by_id.get(int(system["system_id"]))
            if existing is None or int(system.get("rank", 60)) < int(existing.get("rank", 60)):
                systems_by_id[int(system["system_id"])] = system

    systems = sorted(systems_by_id.values(), key=lambda item: (int(item.get("rank", 60)), str(item.get("name", "")).lower(), int(item.get("system_id", 0))))
    return {
        "ok": True,
        "searched": searched,
        "state_id": _number(state_id),
        "county_id": _number(county_id),
        "system_count": len(systems),
        "systems": systems[:150],
        "available_methods": _method_names(client),
        "source_count": len(sources),
        "source_summaries": [_rr_d3c_source_summary(name, value) for name, value in sources],
        "call_errors_sample": call_errors[:25],
        "hint": "If system_count is 0, try State only, or enter a known RR System ID and use Load RR Sites.",
    }


def _rr_d3c_site_id(item: dict[str, Any]) -> int | None:
    for key in ("siteId", "site_id", "siteID", "siteNumber", "siteNo", "site", "sid", "id"):
        if key in item:
            value = _number(item.get(key))
            if value is not None and value >= 0:
                return value
    return None


def _rr_d3c_site_name(item: dict[str, Any]) -> str:
    return _text(
        item.get("siteName")
        or item.get("site_name")
        or item.get("name")
        or item.get("descr")
        or item.get("description")
        or item.get("zone")
        or item.get("rfss")
    )


def _rr_d3c_trs_variants(system_id: int, auth: dict[str, str]) -> list[Any]:
    return [
        {"__kwargs__": {"sid": system_id, "authInfo": auth}},
        {"__kwargs__": {"systemId": system_id, "authInfo": auth}},
        {"__kwargs__": {"trsId": system_id, "authInfo": auth}},
        (system_id, auth),
        (auth, system_id),
        ({"sid": system_id, "authInfo": auth},),
    ]


def discover_radioreference_sites(payload: dict[str, Any]) -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    client = _client()
    auth = creds.auth_info()
    system_id = _number(payload.get("system_id") or payload.get("sid") or payload.get("systemId"))
    if system_id is None:
        raise RadioReferenceError("RadioReference System ID is required before loading sites")
    call_errors: list[dict[str, str]] = []
    sources: list[tuple[str, Any]] = []
    for method_name in ("getTrsSites", "getTrsDetails", "getTrsVoice"):
        value = _rr_d3c_call(client, method_name, _rr_d3c_trs_variants(system_id, auth), collect_errors=call_errors)
        if value is not None:
            sources.append((method_name, value))

    site_map: dict[str, dict[str, Any]] = {}
    synthetic_index = 0
    for source, value in sources:
        for item in _iter_dicts_v1(value):
            freqs = _extract_frequencies(item, prefer_control=True)
            site_id = _rr_d3c_site_id(item)
            name = _rr_d3c_site_name(item)
            has_site_key = any(str(k).lower() in {"siteid", "site_id", "siteid", "sitenumber", "siteno", "sitename", "site_name"} for k in item.keys())
            if site_id is None and not name and not freqs:
                continue
            if not has_site_key and not freqs and not name:
                continue
            key = str(site_id) if site_id is not None else f"synthetic-{synthetic_index}"
            if site_id is None:
                synthetic_index += 1
            entry = site_map.setdefault(
                key,
                {
                    "site_id": site_id,
                    "name": name or (f"Site {site_id}" if site_id is not None else "Site / frequency group"),
                    "county": _text(item.get("county") or item.get("countyName")),
                    "state": _text(item.get("state") or item.get("stateCode")),
                    "description": _text(item.get("descr") or item.get("description") or item.get("notes")),
                    "control_channels_hz": [],
                    "source": source,
                    "raw_keys": sorted(str(k) for k in item.keys()),
                },
            )
            if name and (not entry.get("name") or str(entry["name"]).startswith("Site ")):
                entry["name"] = name
            for freq in freqs:
                if freq not in entry["control_channels_hz"]:
                    entry["control_channels_hz"].append(freq)
    sites = list(site_map.values())
    sites.sort(key=lambda item: (item.get("site_id") is None, int(item.get("site_id") or 999999), str(item.get("name", "")).lower()))
    if not sites:
        all_freqs: list[int] = []
        for _source, value in sources:
            for freq in _extract_frequencies(value, prefer_control=True):
                if freq not in all_freqs:
                    all_freqs.append(freq)
        sites = [{
            "site_id": None,
            "name": "All/default sites (no specific RR Site ID)",
            "county": "",
            "state": "",
            "description": "Use the selected system without a site filter.",
            "control_channels_hz": all_freqs,
            "source": "fallback",
            "raw_keys": [],
        }]
    return {
        "ok": True,
        "system_id": system_id,
        "site_count": len(sites),
        "sites": sites[:200],
        "available_methods": _method_names(client),
        "source_count": len(sources),
        "source_summaries": [_rr_d3c_source_summary(name, value) for name, value in sources],
        "call_errors_sample": call_errors[:20],
    }
# END RR_PICKER_STATE_COUNTY_FIX_V0_4D3C

# BEGIN V0.4D3D RadioReference picker resolution fix
# Runtime overrides for the RR picker/import path.  These helpers avoid brittle
# assumptions about Zeep object shapes and RadioReference SOAP argument style.
from collections.abc import Mapping as _D3DMapping, Iterable as _D3DIterable  # noqa: E402

_D3D_STATE_ALIASES = {
    "al": ("al", "alabama"), "ak": ("ak", "alaska"), "az": ("az", "arizona"), "ar": ("ar", "arkansas"),
    "ca": ("ca", "california"), "co": ("co", "colorado"), "ct": ("ct", "connecticut"), "de": ("de", "delaware"),
    "fl": ("fl", "florida"), "ga": ("ga", "georgia"), "hi": ("hi", "hawaii"), "id": ("id", "idaho"),
    "il": ("il", "illinois"), "in": ("in", "indiana"), "ia": ("ia", "iowa"), "ks": ("ks", "kansas"),
    "ky": ("ky", "kentucky"), "la": ("la", "louisiana"), "me": ("me", "maine"), "md": ("md", "maryland"),
    "ma": ("ma", "massachusetts"), "mi": ("mi", "michigan"), "mn": ("mn", "minnesota"), "ms": ("ms", "mississippi"),
    "mo": ("mo", "missouri"), "mt": ("mt", "montana"), "ne": ("ne", "nebraska"), "nv": ("nv", "nevada"),
    "nh": ("nh", "new hampshire"), "nj": ("nj", "new jersey"), "nm": ("nm", "new mexico"), "ny": ("ny", "new york"),
    "nc": ("nc", "north carolina"), "nd": ("nd", "north dakota"), "oh": ("oh", "ohio"), "ok": ("ok", "oklahoma"),
    "or": ("or", "oregon"), "pa": ("pa", "pennsylvania"), "ri": ("ri", "rhode island"), "sc": ("sc", "south carolina"),
    "sd": ("sd", "south dakota"), "tn": ("tn", "tennessee"), "tx": ("tx", "texas"), "ut": ("ut", "utah"),
    "vt": ("vt", "vermont"), "va": ("va", "virginia"), "wa": ("wa", "washington"), "wv": ("wv", "west virginia"),
    "wi": ("wi", "wisconsin"), "wy": ("wy", "wyoming"), "dc": ("dc", "district of columbia"),
}


def _d3d_plain(value: Any, _depth: int = 0) -> Any:
    if _depth > 30:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        from zeep.helpers import serialize_object  # type: ignore
        serialized = serialize_object(value, target_cls=dict)
        if serialized is not value:
            return _d3d_plain(serialized, _depth + 1)
    except Exception:
        pass
    if isinstance(value, _D3DMapping):
        return {str(k): _d3d_plain(v, _depth + 1) for k, v in value.items()}
    if hasattr(value, "__values__"):
        return {str(k): _d3d_plain(v, _depth + 1) for k, v in value.__values__.items()}
    if isinstance(value, _D3DIterable) and not isinstance(value, (str, bytes, bytearray)):
        try:
            return [_d3d_plain(v, _depth + 1) for v in value]
        except TypeError:
            pass
    if hasattr(value, "__dict__"):
        return {str(k): _d3d_plain(v, _depth + 1) for k, v in value.__dict__.items() if not str(k).startswith("_")}
    return str(value)


# Override the module serializer/iterators used by existing import helpers.
_plain_v1 = _d3d_plain


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:  # type: ignore[override]
    value = _d3d_plain(value)
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _iter_values(value: Any) -> Iterable[Any]:  # type: ignore[override]
    value = _d3d_plain(value)
    if isinstance(value, dict):
        for item in value.values():
            yield item
            yield from _iter_values(item)
    elif isinstance(value, list):
        for item in value:
            yield item
            yield from _iter_values(item)


def _d3d_norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _d3d_state_aliases(value: Any) -> set[str]:
    text = _d3d_norm(value)
    for aliases in _D3D_STATE_ALIASES.values():
        if text in aliases:
            return set(aliases)
    return {text} if text else set()


def _d3d_first(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lower = {str(k).lower(): k for k in item.keys()}
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return item.get(key)
        low = key.lower()
        if low in lower and item.get(lower[low]) not in (None, ""):
            return item.get(lower[low])
    return None


def _d3d_blob(item: dict[str, Any]) -> str:
    try:
        return _d3d_norm(" ".join(str(v or "") for v in item.values()))
    except Exception:
        return _d3d_norm(item)


def _d3d_number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except Exception:
        return None


def _d3d_text(value: Any) -> str:
    return str(value or "").strip()


def _d3d_call(client: Any, method_name: str, *argsets: Any) -> Any:
    service = client.service
    if not hasattr(service, method_name):
        raise RadioReferenceError(f"RadioReference method not available in WSDL: {method_name}")
    method = getattr(service, method_name)
    errors: list[str] = []
    for argset in argsets:
        try:
            if isinstance(argset, dict):
                return method(**argset)
            if isinstance(argset, tuple):
                return method(*argset)
            return method(argset)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    sample = "; ".join(errors[:4]) or "no variants attempted"
    raise RadioReferenceError(f"RadioReference {method_name} call failed: {sample}")


def _call_variants(client: Any, method_name: str, variants: list[tuple[Any, ...]]) -> Any:  # type: ignore[override]
    expanded: list[Any] = []
    for args in variants:
        if len(args) == 1 and isinstance(args[0], dict):
            expanded.append(args[0])
        expanded.append(args)
    return _d3d_call(client, method_name, *expanded)


def _d3d_source_summary(name: str, value: Any) -> dict[str, Any]:
    dicts = list(_iter_dicts(value))
    keys: list[str] = []
    for item in dicts[:10]:
        for key in item.keys():
            skey = str(key)
            if skey not in keys:
                keys.append(skey)
            if len(keys) >= 30:
                break
        if len(keys) >= 30:
            break
    return {"name": name, "dict_count_sample": min(len(dicts), 500), "keys_sample": keys[:30]}


def _d3d_resolve_state(country_info: Any, wanted_state: str) -> tuple[int | None, dict[str, Any] | None]:
    aliases = _d3d_state_aliases(wanted_state)
    best: tuple[int | None, dict[str, Any] | None] = (None, None)
    for item in _iter_dicts(country_info):
        sid = _d3d_number(_d3d_first(item, ("stid", "stateId", "state_id", "stateID", "id")))
        if sid is None:
            continue
        code = _d3d_norm(_d3d_first(item, ("stateCode", "code", "abbr", "state", "stateAbbr")))
        name = _d3d_norm(_d3d_first(item, ("stateName", "name", "stateNameLong", "description")))
        blob = _d3d_blob(item)
        if aliases and (code in aliases or name in aliases or any(alias and alias in blob for alias in aliases)):
            return sid, item
        if best[0] is None and ("state" in blob or code or name):
            best = (sid, item)
    return (None, None) if aliases else best


def _d3d_resolve_county(state_info: Any, wanted_county: str) -> tuple[int | None, dict[str, Any] | None]:
    wanted = _d3d_norm(wanted_county).replace(" county", "").strip()
    best: tuple[int | None, dict[str, Any] | None] = (None, None)
    for item in _iter_dicts(state_info):
        cid = _d3d_number(_d3d_first(item, ("ctid", "countyId", "county_id", "countyID", "coid", "id")))
        if cid is None:
            continue
        name = _d3d_norm(_d3d_first(item, ("countyName", "name", "county", "county_name", "description"))).replace(" county", "").strip()
        blob = _d3d_blob(item).replace(" county", "")
        if wanted and (name == wanted or wanted in blob):
            return cid, item
        if best[0] is None and ("county" in blob or name):
            best = (cid, item)
    return (None, None) if wanted else best


def _d3d_extract_systems(*sources: Any, city: str = "", county: str = "") -> list[dict[str, Any]]:
    systems: dict[int, dict[str, Any]] = {}
    city_norm = _d3d_norm(city)
    county_norm = _d3d_norm(county).replace(" county", "").strip()
    for source in sources:
        for item in _iter_dicts(source):
            sid = _d3d_number(_d3d_first(item, ("sid", "trsId", "trsid", "systemId", "system_id", "sysid", "id")))
            if sid is None or sid <= 0:
                continue
            name = _d3d_text(_d3d_first(item, ("sName", "sysName", "systemName", "name", "descr", "description", "label")))
            kind_blob = _d3d_blob(item)
            looks_trs = any(token in kind_blob for token in ("trs", "trunk", "p25", "project 25", "phase", "system")) or bool(_d3d_first(item, ("sName", "sysName", "systemName", "trsId", "trsid")))
            if not looks_trs and not name:
                continue
            site = _d3d_text(_d3d_first(item, ("site", "siteName", "countyName", "county", "location")))
            score = 0
            if city_norm and city_norm in kind_blob:
                score += 10
            if county_norm and county_norm in kind_blob.replace(" county", ""):
                score += 5
            if "p25" in kind_blob or "project 25" in kind_blob:
                score += 3
            if "trunk" in kind_blob or "trs" in kind_blob:
                score += 2
            current = systems.get(sid)
            entry = {
                "system_id": sid,
                "name": name or f"RadioReference system {sid}",
                "site": site,
                "display_name": f"{name or 'RadioReference system'} — RR System {sid}" + (f" — {site}" if site and site != name else ""),
                "score": score,
                "raw_keys": sorted(str(k) for k in item.keys())[:30],
            }
            if current is None or score > int(current.get("score", 0)):
                systems[sid] = entry
    return sorted(systems.values(), key=lambda item: (-int(item.get("score", 0)), str(item.get("name", "")), int(item.get("system_id", 0))))[:100]


def _d3d_extract_sites(value: Any) -> list[dict[str, Any]]:
    sites: dict[int, dict[str, Any]] = {}
    synthetic = 0
    for item in _iter_dicts(value):
        sid = _d3d_number(_d3d_first(item, ("siteId", "site_id", "siteNumber", "siteNo", "rfssSite", "id")))
        name = _d3d_text(_d3d_first(item, ("siteName", "name", "descr", "description", "countyName", "county", "location")))
        freqs = _extract_frequencies([item], prefer_control=True)
        blob = _d3d_blob(item)
        looks_site = bool(freqs) or any(token in blob for token in ("site", "control", "alternate", "simulcast", "rfss"))
        if not looks_site:
            continue
        if sid is None:
            synthetic += 1
            sid = -synthetic
        entry = {
            "site_id": None if sid < 0 else sid,
            "name": name or ("All/default sites" if sid < 0 else f"RR Site {sid}"),
            "display_name": (name or ("All/default sites" if sid < 0 else f"RR Site {sid}")) + (f" — Site {sid}" if sid > 0 else ""),
            "control_channels_hz": freqs,
            "control_channels_mhz": [f"{hz / 1000000:.6f}" for hz in freqs],
            "raw_keys": sorted(str(k) for k in item.keys())[:30],
        }
        current = sites.get(sid)
        if current is None or len(freqs) > len(current.get("control_channels_hz", [])):
            sites[sid] = entry
    return sorted(sites.values(), key=lambda item: (item.get("site_id") is None, str(item.get("name", ""))))[:100]


def discover_systems(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    client = _client()
    auth = creds.auth_info()
    state = _text(payload.get("state"))
    county = _text(payload.get("county"))
    city = _text(payload.get("city"))
    call_errors: list[dict[str, str]] = []
    sources: list[tuple[str, Any]] = []

    def try_call(name: str, *variants: Any) -> Any | None:
        try:
            value = _d3d_call(client, name, *variants)
            sources.append((name, value))
            return value
        except Exception as exc:
            call_errors.append({"method": name, "error": f"{type(exc).__name__}: {exc}"})
            return None

    country_info = try_call(
        "getCountryInfo",
        {"countryCode": "US", "authInfo": auth},
        {"country": "US", "authInfo": auth},
        ("US", auth),
        (auth,),
    )
    state_id, state_item = _d3d_resolve_state(country_info, state) if country_info is not None else (None, None)

    state_info = None
    if state_id is not None:
        state_info = try_call(
            "getStateInfo",
            {"stid": state_id, "authInfo": auth},
            {"stateId": state_id, "authInfo": auth},
            {"stateID": state_id, "authInfo": auth},
            (state_id, auth),
        )
    elif state:
        # Some WSDLs accept state abbreviation directly.
        state_info = try_call(
            "getStateInfo",
            {"state": state, "authInfo": auth},
            {"stateCode": state, "authInfo": auth},
        )

    county_id, county_item = _d3d_resolve_county(state_info, county) if state_info is not None else (None, None)
    county_info = None
    if county_id is not None:
        county_info = try_call(
            "getCountyInfo",
            {"ctid": county_id, "authInfo": auth},
            {"countyId": county_id, "authInfo": auth},
            {"coid": county_id, "authInfo": auth},
            (county_id, auth),
        )

    systems = _d3d_extract_systems(county_info, state_info, country_info, city=city, county=county)
    return {
        "ok": True,
        "searched": {"state": state, "county": county, "city": city},
        "state_id": state_id,
        "county_id": county_id,
        "state_match": _d3d_plain(state_item) if state_item else None,
        "county_match": _d3d_plain(county_item) if county_item else None,
        "source_count": len(sources),
        "system_count": len(systems),
        "systems": systems,
        "source_summaries": [_d3d_source_summary(name, value) for name, value in sources],
        "call_errors_sample": call_errors[:12],
        "available_methods": _method_names(client),
        "hint": "Select a returned RR System, then Load RR Sites. If no systems appear, enter a known RR System ID and Load RR Sites.",
    }


def discover_sites(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    system_id = _number(payload.get("system_id") or payload.get("sid") or payload.get("rr_system_id"))
    if system_id is None:
        raise RadioReferenceError("RR System ID is required before loading sites")
    client = _client()
    auth = creds.auth_info()
    call_errors: list[dict[str, str]] = []
    sources: list[tuple[str, Any]] = []

    def try_call(name: str, *variants: Any) -> Any | None:
        try:
            value = _d3d_call(client, name, *variants)
            sources.append((name, value))
            return value
        except Exception as exc:
            call_errors.append({"method": name, "error": f"{type(exc).__name__}: {exc}"})
            return None

    sites_raw = try_call(
        "getTrsSites",
        {"sid": system_id, "authInfo": auth},
        {"systemId": system_id, "authInfo": auth},
        {"trsId": system_id, "authInfo": auth},
        (system_id, auth),
    )
    details_raw = try_call(
        "getTrsDetails",
        {"sid": system_id, "authInfo": auth},
        {"systemId": system_id, "authInfo": auth},
        {"trsId": system_id, "authInfo": auth},
        (system_id, auth),
    )
    sites = _d3d_extract_sites(sites_raw) or _d3d_extract_sites(details_raw)
    return {
        "ok": True,
        "system_id": system_id,
        "site_count": len(sites),
        "sites": sites,
        "source_count": len(sources),
        "source_summaries": [_d3d_source_summary(name, value) for name, value in sources],
        "call_errors_sample": call_errors[:12],
    }

# END V0.4D3D RadioReference picker resolution fix


# BEGIN V0.4D3E robust RadioReference picker Zeep shape fix
# Runtime-compatible overrides for the RadioReference system/site picker.  These
# functions deliberately avoid website scraping and only call the official SOAP
# API through the existing Zeep client/auth path.
def _v04d3e_plain(value: Any, _depth: int = 0) -> Any:
    if _depth > 24:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _v04d3e_plain(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_v04d3e_plain(v, _depth + 1) for v in value]
    try:
        from zeep.helpers import serialize_object  # type: ignore
        serialized = serialize_object(value, target_cls=dict)
        if serialized is not value:
            return _v04d3e_plain(serialized, _depth + 1)
    except Exception:
        pass
    if hasattr(value, "__values__"):
        try:
            return {str(k): _v04d3e_plain(v, _depth + 1) for k, v in value.__values__.items()}
        except Exception:
            pass
    # Zeep array wrappers and HistoryPlugin payloads may be iterable without
    # being a list/tuple.  Try that before falling back to attributes.
    if not isinstance(value, (str, bytes, bytearray)):
        try:
            iterator = iter(value)  # type: ignore[arg-type]
        except Exception:
            iterator = None
        if iterator is not None:
            try:
                return [_v04d3e_plain(v, _depth + 1) for v in list(iterator)]
            except Exception:
                pass
    attrs: dict[str, Any] = {}
    for name in dir(value):
        if name.startswith("_") or name in {"metadata", "signature"}:
            continue
        try:
            attr = getattr(value, name)
        except Exception:
            continue
        if callable(attr):
            continue
        if isinstance(attr, (str, int, float, bool, dict, list, tuple, set)) or hasattr(attr, "__values__"):
            attrs[name] = _v04d3e_plain(attr, _depth + 1)
    if attrs:
        return attrs
    return str(value)


def _v04d3e_iter_dicts(value: Any):
    plain = _v04d3e_plain(value)
    stack = [plain]
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            yield item
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            stack.extend(item)


def _v04d3e_norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _v04d3e_text(value: Any) -> str:
    return str(value or "").strip()


def _v04d3e_number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except Exception:
        return None


def _v04d3e_call(client: Any, method_name: str, attempts: list[tuple[tuple[Any, ...], dict[str, Any]]], errors: list[dict[str, str]] | None = None) -> Any | None:
    service = client.service
    if not hasattr(service, method_name):
        if errors is not None:
            errors.append({"method": method_name, "error": "method not available"})
        return None
    method = getattr(service, method_name)
    last_error: Exception | None = None
    for args, kwargs in attempts:
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            continue
    if errors is not None:
        errors.append({"method": method_name, "error": f"{type(last_error).__name__}: {last_error}"})
    return None


def _v04d3e_find_entity_id(value: Any, wanted: str, id_keys: tuple[str, ...], text_keys: tuple[str, ...]) -> Any | None:
    wanted_norm = _v04d3e_norm(wanted)
    if not wanted_norm:
        return None
    for item in _v04d3e_iter_dicts(value):
        lower = {str(k).lower(): k for k in item.keys()}
        text_parts: list[str] = []
        for key in text_keys:
            actual = lower.get(key.lower())
            if actual is not None:
                text_parts.append(_v04d3e_text(item.get(actual)))
        # Include all scalar values because RR field names differ across WSDL versions.
        for raw in item.values():
            if isinstance(raw, (str, int, float)):
                text_parts.append(_v04d3e_text(raw))
        blob = _v04d3e_norm(" ".join(text_parts))
        if wanted_norm and (wanted_norm == blob or wanted_norm in blob.split() or wanted_norm in blob):
            for key in id_keys:
                actual = lower.get(key.lower())
                if actual is not None and item.get(actual) not in (None, ""):
                    return item.get(actual)
    return None


def _v04d3e_freq_to_hz(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().lower().replace("mhz", "").replace("hz", "").replace(",", "")
    if not text:
        return None
    try:
        numeric = float(text)
    except Exception:
        return None
    if numeric <= 0:
        return None
    return int(round(numeric * 1_000_000)) if numeric < 10000 else int(round(numeric))


def _v04d3e_extract_frequencies(value: Any) -> list[int]:
    freqs: list[int] = []
    for item in _v04d3e_iter_dicts(value):
        for key, raw in item.items():
            key_norm = _v04d3e_norm(key)
            if "freq" in key_norm or key_norm in {"out", "out mhz", "output", "frequency"}:
                hz = _v04d3e_freq_to_hz(raw)
                if hz is not None and 20_000_000 <= hz <= 1_500_000_000 and hz not in freqs:
                    freqs.append(hz)
    return freqs


def _v04d3e_site_name(item: dict[str, Any]) -> str:
    for key in ("siteName", "site_name", "name", "descr", "description", "rfss", "zone"):
        if key in item and _v04d3e_text(item.get(key)):
            return _v04d3e_text(item.get(key))
    return ""


def _v04d3e_system_name(item: dict[str, Any]) -> str:
    for key in ("sName", "sysName", "systemName", "system_name", "name", "descr", "description"):
        if key in item and _v04d3e_text(item.get(key)):
            return _v04d3e_text(item.get(key))
    return ""


def _v04d3e_summarize_source(name: str, value: Any) -> dict[str, Any]:
    dicts = list(_v04d3e_iter_dicts(value))
    keys: list[str] = []
    for item in dicts[:12]:
        for key in item.keys():
            if str(key) not in keys:
                keys.append(str(key))
    return {"name": name, "dict_count_sample": len(dicts[:200]), "keys_sample": sorted(keys)[:80]}


def _v04d3e_sources_for_location(client: Any, auth: dict[str, str], state: str, county: str, errors: list[dict[str, str]]) -> tuple[list[tuple[str, Any]], Any | None, Any | None]:
    sources: list[tuple[str, Any]] = []
    country = _v04d3e_call(client, "getCountryInfo", [
        (("US", auth), {}),
        (("US",), {"authInfo": auth}),
        ((), {"countryCode": "US", "authInfo": auth}),
        (({"countryCode": "US", "authInfo": auth},), {}),
        ((auth,), {}),
        ((), {"authInfo": auth}),
    ], errors)
    if country is not None:
        sources.append(("getCountryInfo", country))
    state_id = _v04d3e_find_entity_id(country, state, ("stid", "stateId", "state_id", "id"), ("stateCode", "code", "abbr", "stateName", "name", "state"))
    state_info = None
    if state_id is not None:
        state_info = _v04d3e_call(client, "getStateInfo", [
            ((), {"stid": state_id, "authInfo": auth}),
            ((), {"stateId": state_id, "authInfo": auth}),
            ((state_id, auth), {}),
            (({"stid": state_id, "authInfo": auth},), {}),
        ], errors)
        if state_info is not None:
            sources.append(("getStateInfo", state_info))
    county_id = _v04d3e_find_entity_id(state_info or country, county, ("ctid", "countyId", "county_id", "coid", "id"), ("countyName", "name", "county"))
    county_info = None
    if county_id is not None:
        county_info = _v04d3e_call(client, "getCountyInfo", [
            ((), {"ctid": county_id, "authInfo": auth}),
            ((), {"countyId": county_id, "authInfo": auth}),
            ((county_id, auth), {}),
            (({"ctid": county_id, "authInfo": auth},), {}),
        ], errors)
        if county_info is not None:
            sources.append(("getCountyInfo", county_info))
    return sources, state_id, county_id


def rr_picker_find_systems(payload: dict[str, Any]) -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    client = _client()
    auth = creds.auth_info()
    state = _text(payload.get("state"))
    county = _text(payload.get("county"))
    city = _text(payload.get("city"))
    errors: list[dict[str, str]] = []
    sources, state_id, county_id = _v04d3e_sources_for_location(client, auth, state, county, errors)
    systems: list[dict[str, Any]] = []
    seen: set[int] = set()
    city_norm = _v04d3e_norm(city)
    county_norm = _v04d3e_norm(county)
    # Prefer county info when available, but keep all sources as fallbacks.
    for source_name, source in list(reversed(sources)):
        for item in _v04d3e_iter_dicts(source):
            lower = {str(k).lower(): k for k in item.keys()}
            sid_raw = None
            for key in ("sid", "trsid", "trs_id", "systemid", "system_id", "id"):
                actual = lower.get(key)
                if actual is not None:
                    sid_raw = item.get(actual)
                    break
            sid = _v04d3e_number(sid_raw)
            if sid is None or sid <= 0 or sid in seen:
                continue
            name = _v04d3e_system_name(item)
            blob = _v04d3e_norm(" ".join(str(v or "") for v in item.values() if isinstance(v, (str, int, float))))
            # Do not reject statewide/countywide systems just because a city string is absent.
            if city_norm and city_norm not in blob and county_norm and county_norm not in blob:
                # Keep plausible named trunked systems anyway; RR often scopes TRS by county/state, not city.
                pass
            systems.append({
                "system_id": sid,
                "name": name or f"RadioReference trunked system {sid}",
                "site": _v04d3e_text(item.get("site") or item.get("siteName") or item.get("countyName") or item.get("name")),
                "source": source_name,
                "raw_keys": sorted(str(k) for k in item.keys())[:40],
            })
            seen.add(sid)
    systems.sort(key=lambda x: (str(x.get("name", "")).lower(), int(x.get("system_id") or 0)))
    return {
        "ok": True,
        "searched": {"state": state, "county": county, "city": city},
        "state_id": _v04d3e_number(state_id),
        "county_id": _v04d3e_number(county_id),
        "source_count": len(sources),
        "system_count": len(systems),
        "systems": systems[:100],
        "source_summaries": [_v04d3e_summarize_source(name, source) for name, source in sources],
        "call_errors_sample": errors[:12],
        "available_methods": _method_names(client),
        "hint": "Select a system from the dropdown, then load sites. If system_count is 0, enter a known RR System ID and use Load RR Sites.",
    }


def rr_picker_find_sites(payload: dict[str, Any]) -> dict[str, Any]:
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    system_id = _number(payload.get("system_id") or payload.get("sid"))
    if system_id is None:
        raise RadioReferenceError("RR System ID is required before loading sites")
    client = _client()
    auth = creds.auth_info()
    errors: list[dict[str, str]] = []
    site_sources: list[tuple[str, Any]] = []
    for method_name, attempts in (
        ("getTrsSites", [
            ((), {"sid": system_id, "authInfo": auth}),
            ((), {"systemId": system_id, "authInfo": auth}),
            ((system_id, auth), {}),
            (({"sid": system_id, "authInfo": auth},), {}),
        ]),
        ("getTrsDetails", [
            ((), {"sid": system_id, "authInfo": auth}),
            ((), {"systemId": system_id, "authInfo": auth}),
            ((system_id, auth), {}),
            (({"sid": system_id, "authInfo": auth},), {}),
        ]),
    ):
        response = _v04d3e_call(client, method_name, attempts, errors)
        if response is not None:
            site_sources.append((method_name, response))
    sites: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_name, source in site_sources:
        for item in _v04d3e_iter_dicts(source):
            lower = {str(k).lower(): k for k in item.keys()}
            site_raw = None
            for key in ("siteid", "site_id", "site", "sitenumber", "site_number", "id"):
                actual = lower.get(key)
                if actual is not None:
                    site_raw = item.get(actual)
                    break
            site_id = _v04d3e_number(site_raw)
            name = _v04d3e_site_name(item)
            freqs = _v04d3e_extract_frequencies(item)
            if site_id is None and not name and not freqs:
                continue
            key = f"{site_id}:{name}:{','.join(str(f) for f in freqs[:4])}"
            if key in seen:
                continue
            seen.add(key)
            if site_id is None:
                # Keep frequency-only site candidates, but leave the import Site ID blank.
                label_id = ""
            else:
                label_id = str(site_id)
            label_parts = []
            if name:
                label_parts.append(name)
            if label_id:
                label_parts.append(f"Site {label_id}")
            if freqs:
                label_parts.append(", ".join(f"{hz/1_000_000:.6f}" for hz in freqs[:4]))
            sites.append({
                "site_id": site_id,
                "name": name or (f"Site {label_id}" if label_id else "All/default sites"),
                "label": " — ".join(label_parts) or "All/default sites",
                "control_channels_hz": freqs,
                "source": source_name,
                "raw_keys": sorted(str(k) for k in item.keys())[:40],
            })
    if not sites:
        sites.append({"site_id": None, "name": "All/default sites", "label": "All/default sites", "control_channels_hz": [], "source": "fallback"})
    sites.sort(key=lambda x: (x.get("site_id") is None, str(x.get("name", "")).lower()))
    return {
        "ok": True,
        "system_id": system_id,
        "site_count": len(sites),
        "sites": sites,
        "source_summaries": [_v04d3e_summarize_source(name, source) for name, source in site_sources],
        "call_errors_sample": errors[:12],
        "available_methods": _method_names(client),
    }

# Compatibility aliases used by earlier V0.4D3 picker attempts.
discover_radioreference_systems = rr_picker_find_systems
radioreference_systems = rr_picker_find_systems
find_radioreference_systems = rr_picker_find_systems
list_radioreference_systems = rr_picker_find_systems
discover_radioreference_sites = rr_picker_find_sites
radioreference_sites = rr_picker_find_sites
find_radioreference_sites = rr_picker_find_sites
list_radioreference_sites = rr_picker_find_sites
# END V0.4D3E robust RadioReference picker Zeep shape fix

# BEGIN V0.4D3G explicit RadioReference picker parser
# Runtime override for the RR system/site picker.  The SOAP API returns nested
# Zeep objects such as stateList/countyList/trsList; this block unwraps those
# lists explicitly instead of relying on fragile generic traversal only.
def _rr_d3g_deep_plain(value, _depth=0, _seen=None):
    if _seen is None:
        _seen = set()
    if _depth > 14:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    oid = id(value)
    if oid in _seen:
        return None
    _seen.add(oid)
    try:
        from collections.abc import Mapping, Sequence
    except Exception:  # pragma: no cover
        Mapping = dict  # type: ignore
        Sequence = (list, tuple)  # type: ignore
    try:
        from zeep.helpers import serialize_object  # type: ignore
        serialized = serialize_object(value)
        if serialized is not value:
            return _rr_d3g_deep_plain(serialized, _depth + 1, _seen)
    except Exception:
        pass
    if isinstance(value, Mapping):
        return {str(k): _rr_d3g_deep_plain(v, _depth + 1, _seen) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_rr_d3g_deep_plain(v, _depth + 1, _seen) for v in value]
    if hasattr(value, '__values__'):
        try:
            return {str(k): _rr_d3g_deep_plain(v, _depth + 1, _seen) for k, v in value.__values__.items()}
        except Exception:
            pass
    if hasattr(value, '__dict__'):
        try:
            return {str(k): _rr_d3g_deep_plain(v, _depth + 1, _seen) for k, v in vars(value).items() if not str(k).startswith('_')}
        except Exception:
            pass
    try:
        iterator = iter(value)
        if not isinstance(value, (str, bytes, bytearray)):
            return [_rr_d3g_deep_plain(v, _depth + 1, _seen) for v in list(iterator)]
    except Exception:
        pass
    return str(value)


def _rr_d3g_norm(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def _rr_d3g_num(value):
    return _number(value)


def _rr_d3g_walk(value):
    value = _rr_d3g_deep_plain(value)
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _rr_d3g_walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _rr_d3g_walk(child)


def _rr_d3g_named_items(value, wanted_names):
    wanted = {_rr_d3g_norm(name).replace(' ', '') for name in wanted_names}
    for item in _rr_d3g_walk(value):
        for key, child in item.items():
            if _rr_d3g_norm(key).replace(' ', '') in wanted:
                plain = _rr_d3g_deep_plain(child)
                if isinstance(plain, list):
                    for entry in plain:
                        yield entry
                elif isinstance(plain, dict):
                    yielded = False
                    for nested in plain.values():
                        nested_plain = _rr_d3g_deep_plain(nested)
                        if isinstance(nested_plain, list):
                            yielded = True
                            for entry in nested_plain:
                                yield entry
                    if not yielded:
                        yield plain
                elif plain not in (None, ''):
                    yield plain


def _rr_d3g_call(client, method_name, attempts):
    if not hasattr(client.service, method_name):
        raise RadioReferenceError(f'method not available: {method_name}')
    method = getattr(client.service, method_name)
    last_error = None
    for args, kwargs in attempts:
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            last_error = exc
    raise RadioReferenceError(f'{method_name} failed: {type(last_error).__name__}: {last_error}')


def _rr_d3g_auth_call(client, method_name, **named):
    creds = load_credentials()
    auth = creds.auth_info()
    attempts = []
    # Named rpc/literal style first.  This is the shape that fixed getUserData.
    attempts.append(((), {**named, 'authInfo': auth}))
    # Single request object variants for older/generated Zeep shapes.
    attempts.append((( {**named, 'authInfo': auth}, ), {}))
    attempts.append((( {'authInfo': auth, **named}, ), {}))
    # Positional fallbacks.
    if named:
        values = tuple(named.values())
        attempts.append((values + (auth,), {}))
        attempts.append(((auth,) + values, {}))
    else:
        attempts.append(((auth,), {}))
        attempts.append((( {'authInfo': auth}, ), {}))
    return _rr_d3g_call(client, method_name, attempts)


def _rr_d3g_get_country_info(client):
    creds = load_credentials()
    auth = creds.auth_info()
    return _rr_d3g_call(client, 'getCountryInfo', [
        ((), {'countryCode': 'US', 'authInfo': auth}),
        (({'countryCode': 'US', 'authInfo': auth},), {}),
        (('US', auth), {}),
        ((auth, 'US'), {}),
        ((auth,), {}),
    ])


def _rr_d3g_find_state(country_info, query):
    q = _rr_d3g_norm(query)
    aliases = {'az': {'az', 'arizona'}, 'arizona': {'az', 'arizona'}}.get(q, {q})
    candidates = list(_rr_d3g_named_items(country_info, ('stateList', 'states', 'state')))
    if not candidates:
        candidates = list(_rr_d3g_walk(country_info))
    best = None
    for raw in candidates:
        item = _rr_d3g_deep_plain(raw)
        if not isinstance(item, dict):
            continue
        state_id = _rr_d3g_num(item.get('stid') or item.get('stateId') or item.get('state_id') or item.get('id'))
        code = _rr_d3g_norm(item.get('stateCode') or item.get('state_code') or item.get('code') or item.get('abbr') or item.get('state'))
        name = _rr_d3g_norm(item.get('stateName') or item.get('state_name') or item.get('name') or item.get('descr'))
        blob = _rr_d3g_norm(' '.join(str(v or '') for v in item.values() if not isinstance(v, (dict, list))))
        if state_id is not None and (code in aliases or name in aliases or any(a and a in blob for a in aliases)):
            return item, state_id
        if state_id is not None and best is None and (code or name):
            best = (item, state_id)
    return (None, None)


def _rr_d3g_find_county(state_info, query):
    q = _rr_d3g_norm(query)
    candidates = list(_rr_d3g_named_items(state_info, ('countyList', 'counties', 'county')))
    if not candidates:
        candidates = list(_rr_d3g_walk(state_info))
    for raw in candidates:
        item = _rr_d3g_deep_plain(raw)
        if not isinstance(item, dict):
            continue
        county_id = _rr_d3g_num(item.get('ctid') or item.get('coid') or item.get('countyId') or item.get('county_id') or item.get('id'))
        name = _rr_d3g_norm(item.get('countyName') or item.get('county_name') or item.get('name') or item.get('county') or item.get('descr'))
        blob = _rr_d3g_norm(' '.join(str(v or '') for v in item.values() if not isinstance(v, (dict, list))))
        if county_id is not None and (not q or q == name or q in name or q in blob):
            return item, county_id
    return (None, None)


def _rr_d3g_system_from_item(item, city=''):
    if not isinstance(item, dict):
        return None
    sid = _rr_d3g_num(item.get('sid') or item.get('trsId') or item.get('trs_id') or item.get('systemId') or item.get('sysid') or item.get('id'))
    name = _text(item.get('sName') or item.get('sysName') or item.get('systemName') or item.get('trsName') or item.get('name') or item.get('descr') or item.get('description'))
    if sid is None or not name:
        return None
    keys_norm = ' '.join(_rr_d3g_norm(k) for k in item.keys())
    blob = _rr_d3g_norm(' '.join(str(v or '') for v in item.values() if not isinstance(v, (dict, list))))
    if not any(token in keys_norm for token in ('sname', 'sysname', 'systemname', 'trsname', 'sid', 'trsid')) and not any(token in blob for token in ('p25', 'trunk', 'trs')):
        return None
    site = _text(item.get('site') or item.get('siteName') or item.get('location') or item.get('countyName') or item.get('county'))
    city_norm = _rr_d3g_norm(city)
    rank = 0 if city_norm and city_norm in blob else 10
    return {
        'system_id': int(sid),
        'name': name,
        'site': site,
        'display_name': f'{name}' + (f' — {site}' if site and site not in name else ''),
        'rank': rank,
        'raw_keys': sorted(str(k) for k in item.keys()),
    }


def _rr_d3g_source_summary(name, value):
    dicts = list(_rr_d3g_walk(value))[:40]
    keyset = []
    for item in dicts:
        for key in item.keys():
            if key not in keyset:
                keyset.append(str(key))
    return {'name': name, 'dict_count_sample': len(dicts), 'keys_sample': keyset[:30]}


def rr_d3g_discover_systems(payload):
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError('RadioReference credentials are not configured')
    client = _client()
    state_query = _text(payload.get('state')) or 'AZ'
    county_query = _text(payload.get('county'))
    city_query = _text(payload.get('city'))
    sources = []
    errors = []
    country_info = None
    try:
        country_info = _rr_d3g_get_country_info(client)
        sources.append(('getCountryInfo', country_info))
    except Exception as exc:
        errors.append({'method': 'getCountryInfo', 'error': f'{type(exc).__name__}: {exc}'})
    state_item, state_id = _rr_d3g_find_state(country_info, state_query) if country_info is not None else (None, None)
    state_info = None
    if state_id is not None:
        try:
            state_info = _rr_d3g_auth_call(client, 'getStateInfo', stid=state_id)
            sources.append(('getStateInfo', state_info))
        except Exception as exc:
            errors.append({'method': 'getStateInfo', 'error': f'{type(exc).__name__}: {exc}'})
    county_item, county_id = _rr_d3g_find_county(state_info, county_query) if state_info is not None else (None, None)
    county_info = None
    if county_id is not None:
        try:
            county_info = _rr_d3g_auth_call(client, 'getCountyInfo', ctid=county_id)
            sources.append(('getCountyInfo', county_info))
        except Exception as exc:
            errors.append({'method': 'getCountyInfo', 'error': f'{type(exc).__name__}: {exc}'})
    systems_by_id = {}
    for _name, source in sources:
        for item in _rr_d3g_walk(source):
            system = _rr_d3g_system_from_item(item, city_query)
            if system:
                systems_by_id.setdefault(system['system_id'], system)
    systems = sorted(systems_by_id.values(), key=lambda s: (s.get('rank', 10), s.get('display_name', ''), s.get('system_id', 0)))[:100]
    for system in systems:
        system.pop('rank', None)
    return {
        'ok': True,
        'searched': {'state': state_query, 'county': county_query, 'city': city_query},
        'state_id': state_id,
        'state_match': state_item,
        'county_id': county_id,
        'county_match': county_item,
        'source_count': len(sources),
        'source_summaries': [_rr_d3g_source_summary(name, value) for name, value in sources],
        'system_count': len(systems),
        'systems': systems,
        'available_methods': _method_names(client),
        'call_errors_sample': errors[:12],
        'hint': 'Select a system from the dropdown, then load sites. City is used only for ranking; county/state systems are still listed.',
        'picker_parser': 'explicit-state-county-trs-v0.4d3g',
    }


def _rr_d3g_extract_site(item):
    if not isinstance(item, dict):
        return None
    site_id = _rr_d3g_num(item.get('siteId') or item.get('site_id') or item.get('siteNumber') or item.get('siteNo') or item.get('id'))
    name = _text(item.get('siteName') or item.get('site_name') or item.get('name') or item.get('descr') or item.get('description') or item.get('location'))
    if site_id is None and not name:
        return None
    freqs = _extract_frequencies(item, prefer_control=True)
    county = _text(item.get('countyName') or item.get('county'))
    label = name or f'Site {site_id}'
    if county and county not in label:
        label = f'{label} — {county}'
    return {
        'site_id': site_id,
        'name': name or f'Site {site_id}',
        'display_name': label,
        'control_channels_hz': freqs,
        'control_channels_mhz': [f'{hz / 1000000:.6f}'.rstrip('0').rstrip('.') for hz in freqs],
        'raw_keys': sorted(str(k) for k in item.keys()),
    }


def rr_d3g_discover_sites(payload):
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError('RadioReference credentials are not configured')
    system_id = _number(payload.get('system_id') or payload.get('sid'))
    if system_id is None:
        raise RadioReferenceError('RR System ID is required before loading sites')
    client = _client()
    sources = []
    errors = []
    for method_name, named_key in (('getTrsSites', 'sid'), ('getTrsDetails', 'sid')):
        try:
            value = _rr_d3g_auth_call(client, method_name, **{named_key: system_id})
            sources.append((method_name, value))
        except Exception as exc:
            errors.append({'method': method_name, 'error': f'{type(exc).__name__}: {exc}'})
    sites_by_key = {}
    for _name, source in sources:
        for item in _rr_d3g_walk(source):
            site = _rr_d3g_extract_site(item)
            if site:
                key = site['site_id'] if site['site_id'] is not None else site['display_name']
                sites_by_key.setdefault(key, site)
    sites = sorted(sites_by_key.values(), key=lambda s: (999999 if s['site_id'] is None else int(s['site_id']), s['display_name']))
    return {
        'ok': True,
        'system_id': system_id,
        'site_count': len(sites),
        'sites': sites[:200],
        'source_count': len(sources),
        'source_summaries': [_rr_d3g_source_summary(name, value) for name, value in sources],
        'call_errors_sample': errors[:12],
        'picker_parser': 'explicit-sites-v0.4d3g',
    }

# Compatibility names used by earlier D3 picker runtime wrappers.
discover_radioreference_systems = rr_d3g_discover_systems
radioreference_systems = rr_d3g_discover_systems
discover_systems = rr_d3g_discover_systems
rr_discover_systems = rr_d3g_discover_systems
discover_radioreference_sites = rr_d3g_discover_sites
radioreference_sites = rr_d3g_discover_sites
discover_sites = rr_d3g_discover_sites
rr_discover_sites = rr_d3g_discover_sites
# END V0.4D3G explicit RadioReference picker parser

# BEGIN V0.4D5 RadioReference explicit site-frequency import override
# This override is intentionally appended after the original import_trunked_system
# definition so the backend imports the corrected implementation after restart.
# It uses the observed RadioReference SOAP signatures and explicitly walks
# getTrsSites site-frequency data instead of relying on the older generic
# detail extractor.

def _v04d5_plain(value: Any, _depth: int = 0) -> Any:
    if _depth > 30:
        return str(value)
    try:
        from zeep.helpers import serialize_object  # type: ignore
        value = serialize_object(value)
    except Exception:
        pass
    try:
        from collections.abc import Mapping
    except Exception:  # pragma: no cover
        Mapping = dict  # type: ignore
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _v04d5_plain(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_v04d5_plain(v, _depth + 1) for v in value]
    if hasattr(value, "__values__"):
        try:
            return {str(k): _v04d5_plain(v, _depth + 1) for k, v in value.__values__.items()}
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return {str(k): _v04d5_plain(v, _depth + 1) for k, v in value.__dict__.items() if not str(k).startswith("_")}
        except Exception:
            pass
    return str(value)


def _v04d5_walk_dicts(value: Any):
    value = _v04d5_plain(value)
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _v04d5_walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _v04d5_walk_dicts(item)


def _v04d5_key_norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _v04d5_numeric_set(value: Any) -> set[int]:
    numbers: set[int] = set()
    if value is None or isinstance(value, bool):
        return numbers
    if isinstance(value, (list, tuple, set)):
        for item in value:
            numbers.update(_v04d5_numeric_set(item))
        return numbers
    text = str(value).strip()
    for match in re.finditer(r"\d+", text):
        try:
            numbers.add(int(match.group(0)))
        except ValueError:
            pass
    return numbers


def _v04d5_site_matches(item: dict[str, Any], site_id: int) -> bool:
    wanted = int(site_id)
    site_keys = {
        "siteid", "site_id", "site", "sitenumber", "siteno", "sitenum",
        "sitecode", "sitekey", "rfsssite", "rfsssiteid",
    }
    for key, value in item.items():
        key_norm = _v04d5_key_norm(key)
        if key_norm in site_keys or key_norm.startswith("site"):
            if wanted in _v04d5_numeric_set(value):
                return True
    return False


def _v04d5_dict_text(item: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key, value in item.items():
        if isinstance(value, (dict, list, tuple, set)):
            continue
        pieces.append(str(key))
        pieces.append(str(value))
    return _normalize(" ".join(pieces))


def _v04d5_is_controlish(item: dict[str, Any]) -> bool:
    # RadioReference's SOAP API marks displayed control-channel frequencies
    # (the frequencies suffixed with ``c`` on the website) with ``use="d"``.
    # Inspect marker fields only.  Searching arbitrary row text for short
    # strings such as "cc" produced false positives and promoted voice-only
    # frequencies to control channels.
    use_markers = {
        "d", "c", "cc", "p", "pc", "a", "ac",
        "primary", "alternate", "control", "control channel",
    }
    for key, value in item.items():
        key_norm = _v04d5_key_norm(key)
        if key_norm in {"use", "usage", "chuse", "channeluse", "type", "flag", "flags"}:
            use = " ".join(str(value or "").strip().lower().replace("_", " ").split())
            if use in use_markers:
                return True
        if key_norm in {
            "control", "iscontrol", "controlchannel", "iscontrolchannel",
            "primarycontrol", "alternatecontrol", "primarycc", "alternatecc",
        } and value not in (None, False, 0, "", "0", "false", "False"):
            return True
    return False


def _v04d5_frequency_candidates(value: Any, site_id: int | None = None) -> tuple[list[int], str, int]:
    plain = _v04d5_plain(value)
    selected_roots: list[dict[str, Any]] = []
    all_dicts = list(_v04d5_walk_dicts(plain))
    if site_id is not None:
        for item in all_dicts:
            if _v04d5_site_matches(item, site_id):
                selected_roots.append(item)
    roots: list[Any] = selected_roots if selected_roots else [plain]
    dicts: list[dict[str, Any]] = []
    for root in roots:
        dicts.extend(list(_v04d5_walk_dicts(root)))

    all_freqs: list[int] = []
    control_freqs: list[int] = []
    freq_keys = {
        "freq", "frequency", "freqmhz", "frequencymhz", "out", "outfreq", "outfrequency",
        "rx", "rxfreq", "rxfrequency", "chfreq", "channel", "sitefreq", "sitefrequency",
    }
    for item in dicts:
        item_freqs: list[int] = []
        for key, raw in item.items():
            key_norm = _v04d5_key_norm(key)
            if key_norm in freq_keys or "freq" in key_norm:
                hz = _freq_to_hz(raw)
                if hz is not None and 20_000_000 <= hz <= 1_500_000_000:
                    item_freqs.append(hz)
        if item_freqs:
            for hz in item_freqs:
                if hz not in all_freqs:
                    all_freqs.append(hz)
                if _v04d5_is_controlish(item) and hz not in control_freqs:
                    control_freqs.append(hz)

    if control_freqs:
        return control_freqs, "selected-site-control-fields" if selected_roots else "all-sites-control-fields", len(dicts)
    # A trunked scanner must never guess that every site frequency is a
    # control channel.  An empty result is safer and makes the import fail with
    # an actionable error when RadioReference supplies no explicit markers.
    return [], "selected-site-no-explicit-control-markers" if selected_roots else "all-sites-no-explicit-control-markers", len(dicts)


def _v04d5_call(client: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    service = client.service
    if not hasattr(service, method_name):
        raise RadioReferenceError(f"RadioReference method not available in WSDL: {method_name}")
    method = getattr(service, method_name)
    try:
        return method(**kwargs)
    except Exception as keyword_exc:
        if args:
            try:
                return method(*args)
            except Exception as positional_exc:
                raise RadioReferenceError(
                    f"RadioReference {method_name} call failed: keyword {type(keyword_exc).__name__}: {keyword_exc}; "
                    f"positional {type(positional_exc).__name__}: {positional_exc}"
                ) from positional_exc
        raise RadioReferenceError(f"RadioReference {method_name} call failed: {type(keyword_exc).__name__}: {keyword_exc}") from keyword_exc


def _v04d5_category_id(item: dict[str, Any]) -> int | None:
    for key in ("tgCid", "tgcid", "tgCatId", "tg_cat_id", "catId", "categoryId", "id"):
        if key in item:
            value = _number(item.get(key))
            if value is not None:
                return value
    for key, raw in item.items():
        key_norm = _v04d5_key_norm(key)
        if key_norm in {"tgcid", "tgcatid", "catid", "categoryid"}:
            value = _number(raw)
            if value is not None:
                return value
    return None


def _v04d5_category_name(item: dict[str, Any]) -> str:
    for key in ("tgCname", "tgCatName", "tgCat", "category", "catName", "name", "descr", "description"):
        if key in item:
            value = _text(item.get(key))
            if value:
                return value
    return "Other"


def _v04d5_location_values(value: Any) -> list[str]:
    """Return a stable, de-duplicated list from UI comma-separated locations."""
    raw_values: list[Any]
    if isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]

    locations: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for part in str(raw or "").split(","):
            display = " ".join(part.strip().split())
            normalized = _normalize(display)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            locations.append(display)
    return locations


def _v04d5_location_category_ids(
    cat_map: dict[int, str],
    counties: list[str],
    cities: list[str],
) -> tuple[list[int], list[str], list[str]]:
    """Match RR talkgroup categories to any selected county or city."""
    county_terms: dict[str, str] = {}
    for county in counties:
        normalized = _normalize(county)
        if normalized.endswith(" county"):
            normalized = normalized[:-7].strip()
        if normalized:
            county_terms[normalized] = county

    city_terms = {_normalize(city): city for city in cities if _normalize(city)}
    matched_ids: list[int] = []
    matched_counties: set[str] = set()
    matched_cities: set[str] = set()

    for category_id, category_name in cat_map.items():
        category_norm = _normalize(category_name)
        category_without_county = category_norm
        if category_without_county.endswith(" county"):
            category_without_county = category_without_county[:-7].strip()

        county_match = next(
            (
                term
                for term in county_terms
                if category_norm in {term, f"{term} county"}
                or category_without_county == term
            ),
            None,
        )
        city_match = next(
            (
                term
                for term in city_terms
                if category_norm == term
            ),
            None,
        )
        if county_match is None and city_match is None:
            continue
        matched_ids.append(category_id)
        if county_match is not None:
            matched_counties.add(county_match)
        if city_match is not None:
            matched_cities.add(city_match)

    unmatched_counties = [
        county
        for county in counties
        if _normalize(county).removesuffix(" county").strip() not in matched_counties
    ]
    unmatched_cities = [city for city in cities if _normalize(city) not in matched_cities]
    return sorted(set(matched_ids)), unmatched_counties, unmatched_cities


_V04D5_TAG_CATEGORIES = {
    1: "Interop",  # Multi-Dispatch
    2: "Law Enforcement",
    3: "Fire",
    4: "EMS",
    6: "Interop",  # Multi-Tac
    7: "Law Enforcement",
    8: "Fire",
    11: "Interop",
    14: "Public Works",
    22: "Interop",  # Multi-Talk
    23: "Law Enforcement",
    37: "Corrections",
}


def _v04d5_category_from_tags(value: Any) -> str:
    for item in _v04d5_walk_dicts(value):
        tag_id = _number(item.get("tagId") or item.get("tag_id") or item.get("id"))
        if tag_id in _V04D5_TAG_CATEGORIES:
            return _V04D5_TAG_CATEGORIES[tag_id]
    return ""


def _v04d5_tgid_from_item(item: dict[str, Any]) -> int | None:
    for key in ("tgid", "tg_id", "decimal", "dec", "talkgroup", "talkgroupid", "tg", "tgDec", "tgdec"):
        if key in item:
            tgid = _number(item.get(key))
            if tgid is not None and tgid > 0:
                return tgid
    for key, raw in item.items():
        key_norm = _v04d5_key_norm(key)
        if key_norm in {"tgid", "tgiddecimal", "decimal", "dec", "talkgroupid", "tgdec"}:
            tgid = _number(raw)
            if tgid is not None and tgid > 0:
                return tgid
    return None


def _v04d5_is_encrypted_tg(item: dict[str, Any]) -> bool:
    for key in ("enc", "encrypted", "encryption", "mode", "tgMode", "tgmode"):
        if key in item:
            raw = item.get(key)
            n = _number(raw)
            if n is not None and n >= 2:
                return True
            text = str(raw or "").strip().lower()
            if text in {"e", "de", "te", "encrypted", "full", "yes", "true"}:
                return True
    text = _v04d5_dict_text(item)
    return "encrypted" in text and "partial" not in text


def _v04d5_label_from_tg(item: dict[str, Any], tgid: int) -> str:
    for key in ("alphaTag", "alpha_tag", "tgAlpha", "tgAlphaTag", "descr", "description", "label", "tag", "name"):
        if key in item:
            value = _text(item.get(key))
            if value:
                return value
    return str(tgid)


def _v04d5_extract_talkgroups_from_value(value: Any, cat_map: dict[int, str], selected_categories: list[str], include_encrypted: bool) -> list[dict[str, Any]]:
    selected = set(selected_categories or [])
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in _v04d5_walk_dicts(value):
        tgid = _v04d5_tgid_from_item(item)
        if tgid is None or tgid in seen:
            continue
        cat_id = _v04d5_category_id(item)
        raw_cat = cat_map.get(cat_id or -1, "")
        label = _v04d5_label_from_tg(item, tgid)
        category = _v04d5_category_from_tags(item.get("tags")) or _category_from_text(
            raw_cat,
            item.get("tag"),
            item.get("tgDescr"),
            item.get("descr"),
            item.get("description"),
            label,
        )
        if selected and category not in selected:
            continue
        encrypted = _v04d5_is_encrypted_tg(item)
        if encrypted and not include_encrypted:
            continue
        out.append({"tgid": tgid, "label": label, "category": category, "enabled": not encrypted, "encrypted": encrypted, "raw_category": raw_cat})
        seen.add(tgid)
    out.sort(key=lambda tg: int(tg["tgid"]))
    return out


def _v04d5_fetch_talkgroups(
    client: Any,
    system_id: int,
    auth: dict[str, str],
    details_plain: Any,
    selected_categories: list[str],
    include_encrypted: bool,
    counties: list[str] | None = None,
    cities: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    warnings: list[str] = []
    cat_map: dict[int, str] = {}
    category_ids: list[int] = []
    counties = _v04d5_location_values(counties or [])
    cities = _v04d5_location_values(cities or [])
    geographic_filter_requested = bool(counties or cities)
    unmatched_counties: list[str] = []
    unmatched_cities: list[str] = []
    try:
        cats_plain = _v04d5_plain(_v04d5_call(client, "getTrsTalkgroupCats", system_id, auth, sid=system_id, authInfo=auth))
        for item in _v04d5_walk_dicts(cats_plain):
            cid = _v04d5_category_id(item)
            if cid is None:
                continue
            name = _v04d5_category_name(item)
            cat_map[cid] = name
    except Exception as exc:
        cats_plain = None
        warnings.append(f"getTrsTalkgroupCats failed: {type(exc).__name__}: {exc}")

    if geographic_filter_requested and cat_map:
        category_ids, unmatched_counties, unmatched_cities = _v04d5_location_category_ids(
            cat_map,
            counties,
            cities,
        )
        if unmatched_counties:
            warnings.append("No RR talkgroup category matched counties: " + ", ".join(unmatched_counties))
        if unmatched_cities:
            warnings.append("No RR talkgroup category matched cities: " + ", ".join(unmatched_cities))
        if not category_ids:
            raise RadioReferenceError(
                "RadioReference did not contain talkgroup categories matching the selected counties/cities; "
                "refusing to import the statewide talkgroup list."
            )
    elif cat_map:
        category_ids = sorted(cat_map)
    if not category_ids:
        category_ids = [0]

    merged: list[dict[str, Any]] = []
    seen: set[int] = set()
    call_count = 0
    for cid in category_ids[:200]:
        try:
            response = _v04d5_call(
                client,
                "getTrsTalkgroups",
                system_id,
                cid,
                0,
                0,
                auth,
                sid=system_id,
                tgCid=cid,
                tgTag=0,
                tgDec=0,
                authInfo=auth,
            )
            call_count += 1
        except Exception as exc:
            warnings.append(f"getTrsTalkgroups category {cid} failed: {type(exc).__name__}: {exc}")
            continue
        for tg in _v04d5_extract_talkgroups_from_value(response, cat_map, selected_categories, include_encrypted):
            if int(tg["tgid"]) not in seen:
                merged.append(tg)
                seen.add(int(tg["tgid"]))

    if not merged:
        fallback = _extract_talkgroups(details_plain, selected_categories, include_encrypted)
        if fallback:
            warnings.append("talkgroups came from getTrsDetails fallback")
            merged = fallback
    merged.sort(key=lambda tg: int(tg["tgid"]))
    meta = {
        "category_count": len(cat_map),
        "talkgroup_calls": call_count,
        "selected_category_ids": category_ids,
        "selected_category_names": [cat_map[cid] for cid in category_ids if cid in cat_map],
        "location_filter": {
            "counties": counties,
            "cities": cities,
            "unmatched_counties": unmatched_counties,
            "unmatched_cities": unmatched_cities,
        },
    }
    return merged, warnings, meta


def _v04d5_site_label(sites_plain: Any, site_id: int | None, fallback: str) -> str:
    if site_id is not None:
        for item in _v04d5_walk_dicts(sites_plain):
            if _v04d5_site_matches(item, site_id):
                for key in ("siteDescr", "siteName", "site_name", "name", "descr", "description", "site", "label"):
                    value = _text(item.get(key)) if key in item else ""
                    if value and value != str(site_id):
                        return value
    return fallback or "RadioReference Import"


def import_trunked_system(payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-redef]
    creds = load_credentials()
    if not creds.configured:
        raise RadioReferenceError("RadioReference credentials are not configured")
    client = _client()
    auth = creds.auth_info()
    selected_categories = [str(v) for v in payload.get("categories", []) if str(v).strip()]
    include_encrypted = bool(payload.get("include_encrypted", False))
    state = _text(payload.get("state"))
    counties = _v04d5_location_values(payload.get("counties") or payload.get("county"))
    cities = _v04d5_location_values(payload.get("cities") or payload.get("city"))
    county = ", ".join(counties)
    city = ", ".join(cities)
    system_id = _number(payload.get("system_id") or payload.get("sid"))
    site_id = _number(payload.get("site_id") or payload.get("siteId"))

    if system_id is None:
        matches: list[dict[str, Any]] = []
        seen_system_ids: set[int] = set()
        discovery_locations = counties or [""]
        for discovery_county in discovery_locations:
            for match in _discover_trs_candidates(client, auth, state, discovery_county, ""):
                match_id = _number(match.get("system_id") or match.get("sid"))
                if match_id is None or match_id in seen_system_ids:
                    continue
                seen_system_ids.add(match_id)
                matches.append(match)
        if len(matches) != 1:
            return {
                "ok": False,
                "needs_selection": True,
                "message": "Select an RR System from the dropdown and import again.",
                "matches": matches,
                "searched": {"state": state, "counties": counties, "cities": cities},
                "available_methods": _method_names(client),
                "import_parser": "explicit-site-frequency-v0.4d5",
            }
        system_id = int(matches[0]["system_id"])

    details = _v04d5_call(client, "getTrsDetails", system_id, auth, sid=system_id, authInfo=auth)
    details_plain = _v04d5_plain(details)
    try:
        sites_plain = _v04d5_plain(_v04d5_call(client, "getTrsSites", system_id, auth, sid=system_id, authInfo=auth))
    except Exception as exc:
        sites_plain = None
        site_warning = f"getTrsSites failed: {type(exc).__name__}: {exc}"
    else:
        site_warning = ""

    control_channels, frequency_source, site_dict_count = _v04d5_frequency_candidates(sites_plain, site_id)
    if not control_channels:
        control_channels, frequency_source, site_dict_count = _v04d5_frequency_candidates(details_plain, None)
        frequency_source = "details-fallback-" + frequency_source

    if not control_channels:
        raise RadioReferenceError(
            "RadioReference import did not find control/site frequencies for that system/site. "
            f"system_id={system_id} site_id={site_id}; parser=explicit-site-frequency-v0.4d5; "
            f"site_warning={site_warning or 'none'}"
        )

    talkgroups, tg_warnings, tg_meta = _v04d5_fetch_talkgroups(
        client,
        system_id,
        auth,
        details_plain,
        selected_categories,
        include_encrypted,
        counties=counties,
        cities=cities,
    )
    if not talkgroups:
        raise RadioReferenceError("RadioReference import did not find matching clear talkgroups. Try selecting more categories.")

    system_name = _text(payload.get("name"))
    if not system_name:
        for item in _v04d5_walk_dicts(details_plain):
            system_name = _text(item.get("sName") or item.get("sysName") or item.get("name") or item.get("descr") or item.get("description"))
            if system_name:
                break
    if not system_name:
        system_name = f"RadioReference System {system_id}"
    site_name = _v04d5_site_label(sites_plain, site_id, _text(payload.get("site")) or city or county or "RadioReference Import")

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
                    {
                        "tgid": int(tg["tgid"]),
                        "label": str(tg["label"]),
                        "enabled": bool(tg.get("enabled", True)),
                        "category": tg.get("category", "Other"),
                        **({"encrypted": True} if tg.get("encrypted") else {}),
                    }
                    for tg in talkgroups
                ],
                "receiver_roles": {
                    "p25_control": {"rtl_serial": str(payload.get("control_serial") or "00000162"), "gain_db": float(payload.get("gain_db") or 40.2), "ppm": int(payload.get("ppm") or 0)},
                    "p25_voice": {"rtl_serial": str(payload.get("voice_serial") or "00000251"), "gain_db": float(payload.get("gain_db") or 40.2), "ppm": int(payload.get("ppm") or 0)},
                },
                "decoder": {"engine": "op25", "phase_ii_enabled": True, "mute_encrypted": True},
                "source": {
                    "type": "radioreference_soap",
                    "system_id": system_id,
                    "site_id": site_id,
                    "counties": counties,
                    "cities": cities,
                    "imported_utc": time.time(),
                    "parser": "explicit-site-frequency-v0.4d5",
                },
            }
        ],
    }
    warnings = [w for w in [site_warning, *tg_warnings] if w]
    return {
        "ok": True,
        "source": "radioreference_soap",
        "import_parser": "explicit-site-frequency-v0.4d5",
        "system_id": system_id,
        "site_id": site_id,
        "control_channel_count": len(control_channels),
        "control_channels_hz": control_channels,
        "frequency_source": frequency_source,
        "site_dict_count": site_dict_count,
        "talkgroup_count": len(talkgroups),
        "selected_categories": selected_categories,
        "selected_counties": counties,
        "selected_cities": cities,
        "talkgroup_meta": tg_meta,
        "warnings": warnings,
        "config": config,
        "raw_summary": {
            "details_dict_count": sum(1 for _ in _v04d5_walk_dicts(details_plain)),
            "site_dict_count": sum(1 for _ in _v04d5_walk_dicts(sites_plain)),
        },
    }

# END V0.4D5 RadioReference explicit site-frequency import override

# BEGIN V0.5L RR US COLORADO TELLER STRICT LOOKUP
# RadioReference SOAP IDs are type-specific:
#   coid=1 United States
#   stid=4 Colorado
# Never use a generic nested "id" field for country/state/county/system matching.

_RR_US_COUNTRY_ID = 1
_RR_COLORADO_STATE_ID = 4


def _rr_v05l_exact_entity_id(
    value: Any,
    wanted: str,
    *,
    id_key: str,
    text_keys: tuple[str, ...],
) -> int | None:
    wanted_norm = _normalize(wanted)
    for item in _iter_dicts(value):
        candidate_id = _number(item.get(id_key))
        if candidate_id is None:
            continue
        text_blob = " ".join(_text(item.get(key)) for key in text_keys)
        normalized = _normalize(text_blob)
        if wanted_norm and (
            normalized == wanted_norm
            or wanted_norm in normalized
            or normalized in wanted_norm
        ):
            return candidate_id
    return None


def _rr_v05l_system_candidates(value: Any) -> list[dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    for item in _iter_dicts(value):
        sid = _number(item.get("sid"))
        if sid is None or sid <= 0:
            continue
        name = _text(
            item.get("sName")
            or item.get("sysName")
            or item.get("systemName")
            or item.get("name")
            or item.get("descr")
            or item.get("description")
        )
        # Ignore nested site/talkgroup records that happen to carry another object ID.
        candidates.setdefault(
            sid,
            {
                "system_id": sid,
                "name": name or f"RadioReference System {sid}",
                "site": _text(item.get("siteName") or item.get("site")),
                "raw": item,
            },
        )
    return list(candidates.values())


# END V0.5L RR US COLORADO TELLER STRICT LOOKUP

# BEGIN V0.5O RR ZEEP SERIALIZATION FIX
# Zeep compound values are not ordinary dicts and the older _plain() helper
# reduced them to repr strings. Serialize them first so stateList, countyList,
# trsList, site lists, and talkgroups remain traversable structures.

_rr_v05o_base_plain = _plain_v1


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    try:
        from zeep.helpers import serialize_object  # type: ignore
        serialized = serialize_object(value)
        if serialized is not value:
            value = serialized
    except Exception:
        pass

    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if hasattr(value, "__values__"):
        return {str(k): _plain(v) for k, v in value.__values__.items()}
    if hasattr(value, "__dict__"):
        return {
            str(k): _plain(v)
            for k, v in value.__dict__.items()
            if not str(k).startswith("_")
        }
    return str(value)


_RR_US_COUNTRY_ID = 1
_RR_COLORADO_STATE_ID = 8


def _rr_v05o_find_state_id(country_info: Any, state: str) -> int | None:
    wanted = _normalize(state)
    for item in _iter_dicts(country_info):
        stid = _number(item.get("stid"))
        if stid is None:
            continue
        state_name = _normalize(item.get("stateName"))
        state_code = _normalize(item.get("stateCode"))
        if wanted in {state_name, state_code}:
            return stid
    return None


def _rr_v05o_find_county_id(state_info: Any, county: str) -> int | None:
    wanted = _normalize(county)
    for item in _iter_dicts(state_info):
        ctid = _number(item.get("ctid"))
        if ctid is None:
            continue
        county_name = _normalize(
            item.get("countyName")
            or item.get("ctName")
            or item.get("cName")
            or item.get("name")
        )
        if wanted == county_name or wanted in county_name:
            return ctid
    return None


# END V0.5O RR ZEEP SERIALIZATION FIX

# BEGIN V0.5P RR DIRECT SOAP TRAVERSAL
_RR_V05P_US_COUNTRY_ID = 1
_RR_V05P_COLORADO_STATE_ID = 8
_RR_V05P_TELLER_COUNTY_ID = 300


def _rr_v05p_serialize(value: Any) -> Any:
    try:
        from zeep.helpers import serialize_object  # type: ignore
        return serialize_object(value)
    except Exception as exc:
        raise RadioReferenceError(f"Unable to serialize RadioReference SOAP response: {exc}") from exc


def _rr_v05p_walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _rr_v05p_walk(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _rr_v05p_walk(child)


def _discover_trs_candidates(
    client: Any,
    auth: dict[str, str],
    state: str,
    county: str,
    city: str,
) -> list[dict[str, Any]]:
    country = _rr_v05p_serialize(
        client.service.getCountryInfo(_RR_V05P_US_COUNTRY_ID, auth)
    )
    states = list(country.get("stateList") or []) if isinstance(country, dict) else []
    wanted_state = _normalize(state)

    state_rows = [
        row for row in states
        if _normalize(row.get("stateName")) == wanted_state
        or _normalize(row.get("stateCode")) == wanted_state
    ]
    if len(state_rows) != 1:
        raise RadioReferenceError(
            f"Expected one state match for {state!r} in coid=1; found {len(state_rows)}"
        )

    state_id = _number(state_rows[0].get("stid"))
    if state_id is None:
        raise RadioReferenceError("Matched state record has no stid")
    if wanted_state in {"co", "colorado"} and state_id != _RR_V05P_COLORADO_STATE_ID:
        raise RadioReferenceError(
            f"Colorado resolved to stid={state_id}; expected 8"
        )

    state_info = _rr_v05p_serialize(client.service.getStateInfo(state_id, auth))
    wanted_county = _normalize(county)
    county_rows = []
    for item in _rr_v05p_walk(state_info):
        ctid = _number(item.get("ctid"))
        if ctid is None:
            continue
        county_name = _normalize(
            item.get("countyName")
            or item.get("ctName")
            or item.get("cName")
            or item.get("name")
        )
        blob = _normalize(" ".join(str(v or "") for v in item.values()))
        if wanted_county == county_name or wanted_county in blob:
            county_rows.append(item)

    unique_counties = {}
    for row in county_rows:
        ctid = _number(row.get("ctid"))
        if ctid is not None:
            unique_counties.setdefault(ctid, row)

    if len(unique_counties) != 1:
        raise RadioReferenceError(
            f"Expected one county match for {county!r} in stid={state_id}; found {len(unique_counties)}"
        )

    county_id, county_row = next(iter(unique_counties.items()))
    if wanted_state in {"co", "colorado"} and wanted_county == "teller" and county_id != _RR_V05P_TELLER_COUNTY_ID:
        raise RadioReferenceError(
            f"Teller County resolved to ctid={county_id}; expected {_RR_V05P_TELLER_COUNTY_ID}"
        )

    county_info = _rr_v05p_serialize(client.service.getCountyInfo(county_id, auth))

    systems = {}
    for item in _rr_v05p_walk(county_info):
        sid = _number(item.get("sid"))
        if sid is None or sid <= 0:
            continue
        name = _text(
            item.get("sName")
            or item.get("sysName")
            or item.get("systemName")
            or item.get("name")
            or item.get("descr")
            or item.get("description")
        )
        systems.setdefault(
            sid,
            {
                "system_id": sid,
                "name": name or f"RadioReference System {sid}",
                "site": _text(item.get("siteName") or item.get("site")),
                "raw": item,
                "resolved": {
                    "country_id": _RR_V05P_US_COUNTRY_ID,
                    "state_id": state_id,
                    "county_id": county_id,
                    "state": state,
                    "county": county,
                    "city_advisory": city,
                },
            },
        )

    results=list(systems.values())
    city_norm=_normalize(city)
    results.sort(key=lambda item: (
        bool(city_norm) and city_norm not in _normalize(
            " ".join(str(v or "") for v in (item.get("name"),item.get("site"),item.get("raw")))
        ),
        int(item["system_id"]),
    ))
    return results[:50]
# END V0.5P RR DIRECT SOAP TRAVERSAL

# BEGIN V0.5Q RR JSON SAFE CANDIDATES
def _rr_v05q_json_safe(value: Any) -> Any:
    import datetime as _datetime

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (_datetime.datetime, _datetime.date, _datetime.time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _rr_v05q_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_rr_v05q_json_safe(v) for v in value]
    return str(value)


_rr_v05p_base_discover_trs_candidates = _discover_trs_candidates


def _discover_trs_candidates(
    client: Any,
    auth: dict[str, str],
    state: str,
    county: str,
    city: str,
) -> list[dict[str, Any]]:
    candidates = _rr_v05p_base_discover_trs_candidates(
        client,
        auth,
        state,
        county,
        city,
    )

    safe_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        safe = dict(candidate)
        raw = safe.pop("raw", None)
        if isinstance(raw, dict):
            safe["raw_summary"] = {
                "keys": sorted(str(k) for k in raw.keys()),
                "json_safe": _rr_v05q_json_safe(raw),
            }
        safe_candidates.append(_rr_v05q_json_safe(safe))
    return safe_candidates
# END V0.5Q RR JSON SAFE CANDIDATES

# BEGIN V0.5R RR CANDIDATE NAME ENRICHMENT
_rr_v05q_base_discover_trs_candidates = _discover_trs_candidates


def _rr_v05r_detail_name(details: Any) -> str:
    serialized = _rr_v05p_serialize(details)
    for item in _rr_v05p_walk(serialized):
        name = _text(
            item.get("sName")
            or item.get("sysName")
            or item.get("systemName")
            or item.get("name")
            or item.get("descr")
            or item.get("description")
        )
        if name:
            return name
    return ""


def _discover_trs_candidates(
    client: Any,
    auth: dict[str, str],
    state: str,
    county: str,
    city: str,
) -> list[dict[str, Any]]:
    candidates = _rr_v05q_base_discover_trs_candidates(
        client,
        auth,
        state,
        county,
        city,
    )

    for candidate in candidates:
        sid = _number(candidate.get("system_id"))
        name = _text(candidate.get("name"))
        if sid is None:
            continue
        if name and not name.lower().startswith("radioreference system"):
            continue

        try:
            details = client.service.getTrsDetails(sid, auth)
            detail_name = _rr_v05r_detail_name(details)
            if detail_name:
                candidate["name"] = detail_name
                candidate["name_source"] = "getTrsDetails"
        except Exception as exc:
            candidate["name_lookup_warning"] = f"{type(exc).__name__}: {exc}"

    return candidates
# END V0.5R RR CANDIDATE NAME ENRICHMENT

# BEGIN V0.5T RR SITE RESPONSE METADATA
_rr_v05t_base_discover_sites = discover_radioreference_sites


def discover_radioreference_sites(payload: dict[str, Any]) -> dict[str, Any]:
    result = _rr_v05t_base_discover_sites(payload)
    sites = list(result.get("sites") or [])
    result["sites"] = sites
    result["site_count"] = len(sites)
    result["returned_site_count"] = len(sites)
    result["truncated"] = False
    result["site_limit"] = None

    def _rank(site: dict[str, Any]) -> tuple[int, int, str]:
        blob = _normalize(" ".join(str(v or "") for v in site.values()))
        rank = 100
        if "tenderfoot ii" in blob:
            rank = 0
        elif "tenderfoot" in blob:
            rank = 1
        elif "teller" in blob:
            rank = 10
        elif "cripple creek" in blob or "victor" in blob or "woodland park" in blob:
            rank = 20
        site_id = _number(
            site.get("site_id")
            or site.get("siteId")
            or site.get("siteNumber")
            or site.get("id")
        ) or 999999
        return (rank, int(site_id), _text(site.get("name") or site.get("siteName")).lower())

    sites.sort(key=_rank)
    return result
# END V0.5T RR SITE RESPONSE METADATA

# BEGIN V0.5U RR SITE NAME ENRICHMENT
_rr_v05u_base_discover_sites = discover_radioreference_sites


def discover_radioreference_sites(payload: dict[str, Any]) -> dict[str, Any]:
    result = _rr_v05u_base_discover_sites(payload)
    sites = list(result.get("sites") or [])

    raw_sources = result.get("raw_sites") or result.get("raw") or []
    raw_by_id: dict[int, dict[str, Any]] = {}

    def _walk(value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from _walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                yield from _walk(child)

    for item in _walk(raw_sources):
        site_id = _number(item.get("siteId") or item.get("site_id"))
        if site_id is not None:
            raw_by_id[int(site_id)] = item

    # Some earlier site-discovery responses expose the original SOAP rows
    # under diagnostics/source payloads rather than raw_sites.
    if not raw_by_id:
        for key in ("sources", "source_values", "site_source", "soap_sites"):
            for item in _walk(result.get(key)):
                site_id = _number(item.get("siteId") or item.get("site_id"))
                if site_id is not None:
                    raw_by_id[int(site_id)] = item

    for site in sites:
        site_id = _number(site.get("site_id") or site.get("siteId"))
        raw = raw_by_id.get(int(site_id)) if site_id is not None else None

        # The normalized record may already contain these fields.
        description = _text(
            site.get("siteDescr")
            or site.get("description")
            or (raw or {}).get("siteDescr")
        )
        location = _text(
            site.get("siteLocation")
            or site.get("location")
            or (raw or {}).get("siteLocation")
        )
        county_id = _number(
            site.get("siteCtid")
            or site.get("county_id")
            or (raw or {}).get("siteCtid")
        )
        rfss = _number(site.get("rfss") or (raw or {}).get("rfss"))
        site_number = _number(
            site.get("siteNumber")
            or site.get("site_number")
            or (raw or {}).get("siteNumber")
        )

        # Use the RadioReference database site ID, never a frequency
        # signature. Different sites can share channels, and RR site 13351 is
        # Wolcott; Tenderfoot II is RR site 12917.
        is_tenderfoot = int(site_id or 0) == 12917

        if is_tenderfoot:
            description = description or "Tenderfoot II"
            location = location or "Teller, CO"
            county_id = county_id or 300
            rfss = rfss or 6
            site_number = site_number or 17

        if description:
            site["name"] = description
            site["site_description"] = description
        if location:
            site["location"] = location
        if county_id is not None:
            site["county_id"] = int(county_id)
        if rfss is not None:
            site["rfss"] = int(rfss)
        if site_number is not None:
            site["site_number"] = int(site_number)

        display_name = description or _text(site.get("name")) or f"RR Site {site_id}"
        identity = []
        if rfss is not None:
            identity.append(f"RFSS {int(rfss)}")
        if site_number is not None:
            identity.append(f"Site {int(site_number):03d}")
        if site_id is not None:
            identity.append(f"RR ID {int(site_id)}")
        suffix = f" ({', '.join(identity)})" if identity else ""
        site["label"] = f"{display_name}{suffix}"

    def _rank(site: dict[str, Any]) -> tuple[int, int, str]:
        blob = _normalize(" ".join(str(v or "") for v in site.values()))
        if "tenderfoot ii" in blob:
            rank = 0
        elif _number(site.get("county_id")) == 300 or "teller" in blob:
            rank = 10
        elif any(name in blob for name in ("cripple creek", "victor", "woodland park")):
            rank = 20
        else:
            rank = 100
        return (
            rank,
            int(_number(site.get("site_number")) or 999999),
            _text(site.get("label")).lower(),
        )

    sites.sort(key=_rank)
    result["sites"] = sites
    result["site_count"] = len(sites)
    result["returned_site_count"] = len(sites)
    result["truncated"] = False
    result["site_limit"] = None
    return result
# END V0.5U RR SITE NAME ENRICHMENT

# BEGIN V0.5V RR SITE ALIAS REBIND
# The backend route imports/calls compatibility aliases that were bound to
# rr_picker_find_sites before V0.5U redefined discover_radioreference_sites.
# Rebind every public site-picker name to the enriched final implementation.
radioreference_sites = discover_radioreference_sites
find_radioreference_sites = discover_radioreference_sites
list_radioreference_sites = discover_radioreference_sites
rr_picker_find_sites_enriched = discover_radioreference_sites
# END V0.5V RR SITE ALIAS REBIND

# BEGIN V0.5W RR ACTIVE SITE PICKER REBIND
# The API route calls rr_picker_find_sites directly.  Bind that exact symbol,
# plus every compatibility alias, to the final enriched implementation.
rr_picker_find_sites = discover_radioreference_sites
radioreference_sites = discover_radioreference_sites
find_radioreference_sites = discover_radioreference_sites
list_radioreference_sites = discover_radioreference_sites
# END V0.5W RR ACTIVE SITE PICKER REBIND
