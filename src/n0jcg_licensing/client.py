"""Reusable phone-home licensing client for N0JCG applications."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

DEFAULT_API_URL = "https://www.n0jcg.com/api/v1/licenses/validate"
DEFAULT_REFRESH_SECONDS = 24 * 60 * 60
LICENSE_PATTERN = re.compile(r"^N0JCG-[A-Z0-9]{3}(?:-[A-Z2-9]{4}){4}$")
INSTALLATION_PATTERN = re.compile(r"^N0JCG-[0-9A-F]{4}(?:-[0-9A-F]{4}){3}$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PUBLIC_KEYS = {
    "n0jcg-license-rsa-2026-01": {
        "algorithm": "RSASSA-PKCS1-v1_5-SHA256",
        "exponent": 65537,
        "modulus_hex": (
            "D32C74548CC99C5F8955E5EFABD02BEF79C5B7D937B7EB8265AABB0EFAA2D907"
            "C5EF3C23E7BD68EE54505F57A98E6C451AA2E1EDEFBBCB8BA6BC18DF71BDC6083"
            "A3305CC180380B29A3D5C5A129762FB548629433C2E576BA1FD8F5FA5413758853"
            "ECD22D5D842A6AA43A4151BD9FF526135874EAFF2168B739CB3B5164534837766D"
            "EB99252D83077AAE70FAAD10B47D24E93E8C3DEF1E237E6A0EC86772669E68C7E"
            "B32DD873CF3024B71AAE132AAC70AF505C8D3D32BA79B3B6706C959508DD39AC59"
            "34EF5353236FFA8DF9DB2327F9B4137EFB6A664B7E922742948AF5D499C5C30C69"
            "033F4CF3132E8327874E6DE3C8316F1BBCAD334A443A326EDA22A5"
        ),
    },
}
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class LicenseError(RuntimeError):
    """Raised when activation or signed-lease validation fails."""


def _cpu_serial(cpuinfo_path: Path = Path("/proc/cpuinfo")) -> str:
    try:
        for line in cpuinfo_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() == "serial" and value.strip():
                return value.strip()
    except OSError:
        pass
    return ""


def installation_serial_from_identity(identity: str) -> str:
    clean = str(identity or "").strip() or "unknown-installation"
    digest = hashlib.sha256(f"N0JCG-INSTALLATION\0{clean}".encode("utf-8")).hexdigest()
    groups = [digest[index : index + 4].upper() for index in range(0, 16, 4)]
    return "N0JCG-" + "-".join(groups)


def installation_serial(environment: Mapping[str, str] | None = None) -> str:
    values = os.environ if environment is None else environment
    identity = str(values.get("N0JCG_INSTALLATION_ID", "")).strip()
    identity = identity or str(values.get("N0JCG_SCANNER_INSTALLATION_ID", "")).strip()
    identity = identity or _cpu_serial()
    if not identity:
        try:
            identity = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
        except OSError:
            identity = ""
    return installation_serial_from_identity(identity)


def _b64url_decode(value: str) -> bytes:
    raw = str(value or "").replace("-", "+").replace("_", "/")
    return base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True)


def canonical_lease_json(lease: Mapping[str, Any]) -> bytes:
    canonical = {
        "version": lease.get("version"),
        "key_id": lease.get("key_id"),
        "license_id": lease.get("license_id"),
        "license_suffix": lease.get("license_suffix"),
        "product_slug": lease.get("product_slug"),
        "installation_serial": lease.get("installation_serial"),
        "email_hash": lease.get("email_hash"),
        "issued_at": lease.get("issued_at"),
        "expires_at": lease.get("expires_at"),
        "grace_until": lease.get("grace_until"),
    }
    return json.dumps(canonical, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def verify_rsa_sha256_signature(
    message: bytes,
    signature: bytes,
    *,
    modulus_hex: str,
    exponent: int,
) -> bool:
    modulus = int(modulus_hex, 16)
    width = (modulus.bit_length() + 7) // 8
    if len(signature) != width:
        return False
    encoded = pow(int.from_bytes(signature, "big"), int(exponent), modulus).to_bytes(
        width,
        "big",
    )
    digest_info = _SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(message).digest()
    padding_length = width - len(digest_info) - 3
    if padding_length < 8:
        return False
    expected = b"\x00\x01" + (b"\xff" * padding_length) + b"\x00" + digest_info
    return hmac.compare_digest(encoded, expected)


class LicenseClient:
    """Validate, cache, and periodically refresh one N0JCG product license."""

    def __init__(
        self,
        *,
        product_slug: str,
        app_version: str,
        state_root: Path,
        api_url: str = DEFAULT_API_URL,
        environment: Mapping[str, str] | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.product_slug = str(product_slug).strip().lower()
        self.app_version = str(app_version).strip()
        self.state_root = Path(state_root)
        self.environment = os.environ if environment is None else environment
        # Production licensing is intentionally pinned to the public Worker
        # endpoint. Do not allow a stale service environment variable to send
        # activation requests to another path or host.
        self.api_url = DEFAULT_API_URL
        self.credentials_path = self.state_root / "license_credentials.json"
        self.lease_path = self.state_root / "license_lease.json"
        self._opener = opener
        self._now = now
        self._lock = threading.RLock()
        self._last_validation_error = ""
        self._last_validation_epoch: float | None = None
        self._stop_event = threading.Event()
        self._refresh_thread: threading.Thread | None = None

    @property
    def serial_number(self) -> str:
        return installation_serial(self.environment)

    @staticmethod
    def _normalize_credentials(license_serial: str, email: str) -> dict[str, str]:
        normalized_license = str(license_serial or "").strip().upper()
        normalized_email = str(email or "").strip().lower()
        if not LICENSE_PATTERN.fullmatch(normalized_license):
            raise LicenseError("license S/N format is invalid")
        if len(normalized_email) > 254 or not EMAIL_PATTERN.fullmatch(normalized_email):
            raise LicenseError("email address is invalid")
        return {"license_serial": normalized_license, "email": normalized_email}

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(dict(value), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _verify_response(self, response: Mapping[str, Any], email: str) -> dict[str, Any]:
        lease = response.get("lease")
        signature = str(response.get("signature") or "")
        if not isinstance(lease, dict) or not signature:
            raise LicenseError("validation response did not contain a signed lease")
        key_id = str(lease.get("key_id") or "")
        key = PUBLIC_KEYS.get(key_id)
        if not key:
            raise LicenseError(f"unknown license signing key: {key_id}")
        try:
            valid_signature = verify_rsa_sha256_signature(
                canonical_lease_json(lease),
                _b64url_decode(signature),
                modulus_hex=str(key["modulus_hex"]),
                exponent=int(key["exponent"]),
            )
        except Exception as exc:
            raise LicenseError("license lease signature is invalid") from exc
        if not valid_signature:
            raise LicenseError("license lease signature is invalid")
        expected_email_hash = hashlib.sha256(email.encode("utf-8")).hexdigest()
        if lease.get("product_slug") != self.product_slug:
            raise LicenseError("license lease is for a different product")
        if lease.get("installation_serial") != self.serial_number:
            raise LicenseError("license lease is for a different installation")
        if lease.get("email_hash") != expected_email_hash:
            raise LicenseError("license lease email binding does not match")
        return dict(lease)

    def _request_validation(self, credentials: Mapping[str, str]) -> dict[str, Any]:
        payload = {
            **credentials,
            "installation_serial": self.serial_number,
            "product_slug": self.product_slug,
            "app_version": self.app_version,
        }
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"N0JCG-Scanner/{self.app_version}",
            },
            method="POST",
        )
        try:
            response = self._opener(request, timeout=10)
            body = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                error_payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                error_payload = {}
            reason = str(error_payload.get("error") or error_payload.get("reason") or f"HTTP {exc.code}")
            if exc.code not in (400, 401, 403):
                raise ConnectionError(f"license server unavailable: {reason}") from exc
            raise LicenseError(reason) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(f"license server unavailable: {exc}") from exc
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectionError("license server returned invalid JSON") from exc
        if not isinstance(result, dict) or not result.get("valid"):
            raise LicenseError(str(result.get("error") or "license was rejected"))
        return result

    def activate(self, license_serial: str, email: str) -> dict[str, Any]:
        credentials = self._normalize_credentials(license_serial, email)
        response = self._request_validation(credentials)
        lease = self._verify_response(response, credentials["email"])
        with self._lock:
            self._write_private_json(self.credentials_path, credentials)
            self._write_private_json(
                self.lease_path,
                {"lease": lease, "signature": response["signature"]},
            )
            self._last_validation_error = ""
            self._last_validation_epoch = self._now()
        return self.status()

    def refresh(self) -> dict[str, Any]:
        credentials = self._read_json(self.credentials_path)
        if not credentials:
            return self.status()
        try:
            normalized = self._normalize_credentials(
                str(credentials.get("license_serial") or ""),
                str(credentials.get("email") or ""),
            )
            response = self._request_validation(normalized)
            lease = self._verify_response(response, normalized["email"])
        except ConnectionError as exc:
            with self._lock:
                self._last_validation_error = str(exc)
                self._last_validation_epoch = self._now()
            return self.status()
        except LicenseError as exc:
            with self._lock:
                self._last_validation_error = str(exc)
                self._last_validation_epoch = self._now()
                try:
                    self.lease_path.unlink()
                except FileNotFoundError:
                    pass
            return self.status()
        with self._lock:
            self._write_private_json(
                self.lease_path,
                {"lease": lease, "signature": response["signature"]},
            )
            self._last_validation_error = ""
            self._last_validation_epoch = self._now()
        return self.status()

    def status(self) -> dict[str, Any]:
        now = int(self._now())
        credentials = self._read_json(self.credentials_path)
        cached = self._read_json(self.lease_path)
        lease = cached.get("lease") if isinstance(cached.get("lease"), dict) else {}
        signature = str(cached.get("signature") or "")
        verified = False
        if lease and signature and credentials:
            try:
                normalized = self._normalize_credentials(
                    str(credentials.get("license_serial") or ""),
                    str(credentials.get("email") or ""),
                )
                self._verify_response({"lease": lease, "signature": signature}, normalized["email"])
                verified = True
            except LicenseError:
                verified = False
        expires_at = int(lease.get("expires_at") or 0) if verified else 0
        grace_until = int(lease.get("grace_until") or 0) if verified else 0
        registered = verified and now <= grace_until
        online_valid = registered and now <= expires_at
        return {
            "serial_number": self.serial_number,
            "registered": registered,
            "mode": "registered" if registered else "trial",
            "license_configured": bool(credentials),
            "license_suffix": str(lease.get("license_suffix") or "") if verified else "",
            "online_valid": online_valid,
            "offline_grace": registered and not online_valid,
            "lease_expires_epoch": expires_at or None,
            "grace_until_epoch": grace_until or None,
            "last_validation_epoch": self._last_validation_epoch,
            "validation_error": self._last_validation_error or None,
            "trial_limit_seconds": None if registered else 300,
        }

    def start_background_refresh(self, interval_seconds: int = DEFAULT_REFRESH_SECONDS) -> None:
        if self._refresh_thread and self._refresh_thread.is_alive():
            return

        def worker() -> None:
            self.refresh()
            while not self._stop_event.wait(max(300, int(interval_seconds))):
                self.refresh()

        self._refresh_thread = threading.Thread(
            target=worker,
            name=f"n0jcg-license-{self.product_slug}",
            daemon=True,
        )
        self._refresh_thread.start()

    def close(self) -> None:
        self._stop_event.set()
