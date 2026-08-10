import hashlib
import io
import json
import shutil
import subprocess
import threading
import urllib.error
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest

from n0jcg_licensing.client import DEFAULT_API_URL, LicenseClient, verify_rsa_sha256_signature
from pi_p25_scanner import backend
from pi_p25_scanner.backend import ScannerManager, ScannerStatus
from pi_p25_scanner.registration import (
    installation_serial,
    installation_serial_from_identity,
    valid_installation_serial,
)


def test_installation_serial_is_stable_and_non_reversible() -> None:
    first = installation_serial_from_identity("raspberry-pi-hardware-id")
    second = installation_serial_from_identity("raspberry-pi-hardware-id")
    different = installation_serial_from_identity("different-hardware-id")

    assert first == second
    assert first != different
    assert valid_installation_serial(first)
    assert first.startswith("N0JCG-")


def test_unregistered_installation_defaults_to_five_minutes(tmp_path: Path) -> None:
    client = LicenseClient(
        product_slug="scanner",
        app_version="3.1.0",
        state_root=tmp_path,
        environment={
            "N0JCG_SCANNER_INSTALLATION_ID": "unregistered-host",
        },
    )
    status = client.status()

    assert status["registered"] is False
    assert status["mode"] == "trial"
    assert status["trial_limit_seconds"] == 300


def test_scanner_backend_uses_worker_contract_version() -> None:
    assert backend.APP_VERSION == "3.1.0"


def test_license_client_is_pinned_to_production_worker() -> None:
    client = LicenseClient(
        product_slug="scanner",
        app_version="3.1.0",
        state_root=Path("/tmp/n0jcg-license-test"),
        environment={"N0JCG_LICENSE_API_URL": "https://stale.example.invalid"},
    )
    assert client.api_url == DEFAULT_API_URL


class FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_phone_home_activation_verifies_and_caches_signed_lease(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = 1_800_000_000
    environment = {"N0JCG_SCANNER_INSTALLATION_ID": "test-radio-host"}
    installation = installation_serial(environment)

    def opener(request, timeout):
        assert timeout == 10
        submitted = json.loads(request.data.decode("utf-8"))
        assert request.get_header("User-agent") == "N0JCG-Scanner/3.1.0"
        lease = {
            "version": 1,
            "key_id": "test-key",
            "license_id": "license-1",
            "license_suffix": "JKLM-2345",
            "product_slug": "scanner",
            "installation_serial": installation,
            "email_hash": hashlib.sha256(b"user@example.com").hexdigest(),
            "issued_at": now,
            "expires_at": now + 86400,
            "grace_until": now + 604800,
        }
        assert submitted["license_serial"] == "N0JCG-SCN-ABCD-EFGH-JKLM-2345"
        assert submitted["email"] == "user@example.com"
        assert submitted["installation_serial"] == installation
        assert submitted["product_slug"] == "scanner"
        assert submitted["app_version"] == "3.1.0"
        return FakeHttpResponse({"valid": True, "lease": lease, "signature": "test"})

    client = LicenseClient(
        product_slug="scanner",
        app_version="3.1.0",
        state_root=tmp_path,
        environment=environment,
        opener=opener,
        now=lambda: now,
    )
    monkeypatch.setattr(
        client,
        "_verify_response",
        lambda response, _email: dict(response["lease"]),
    )
    status = client.activate(
        "N0JCG-SCN-ABCD-EFGH-JKLM-2345",
        "User@Example.com",
    )

    assert status["registered"] is True
    assert status["online_valid"] is True
    assert status["license_suffix"] == "JKLM-2345"
    assert client.credentials_path.exists()
    assert client.lease_path.exists()


def test_cached_lease_survives_network_failure_only_through_offline_grace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = [1_800_000_000]
    environment = {"N0JCG_INSTALLATION_ID": "shared-appliance-host"}
    installation = installation_serial(environment)
    lease = {
        "version": 1,
        "key_id": "test-key",
        "license_id": "license-1",
        "license_suffix": "JKLM-2345",
        "product_slug": "scanner",
        "installation_serial": installation,
        "email_hash": hashlib.sha256(b"user@example.com").hexdigest(),
        "issued_at": clock[0],
        "expires_at": clock[0] + 86400,
        "grace_until": clock[0] + 604800,
    }
    calls = 0

    def opener(_request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeHttpResponse({"valid": True, "lease": lease, "signature": "test"})
        raise urllib.error.URLError("offline")

    client = LicenseClient(
        product_slug="scanner",
        app_version="3.1.0",
        state_root=tmp_path,
        environment=environment,
        opener=opener,
        now=lambda: clock[0],
    )
    monkeypatch.setattr(
        client,
        "_verify_response",
        lambda response, _email: dict(response["lease"]),
    )
    client.activate("N0JCG-SCN-ABCD-EFGH-JKLM-2345", "user@example.com")

    clock[0] = lease["expires_at"] + 1
    grace_status = client.refresh()
    assert grace_status["registered"] is True
    assert grace_status["online_valid"] is False
    assert grace_status["offline_grace"] is True

    clock[0] = lease["grace_until"] + 1
    expired_status = client.status()
    assert expired_status["registered"] is False
    assert expired_status["mode"] == "trial"


def test_definitive_license_rejection_removes_cached_lease(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = LicenseClient(
        product_slug="scanner",
        app_version="3.1.0",
        state_root=tmp_path,
        environment={"N0JCG_INSTALLATION_ID": "rejected-appliance"},
    )
    client._write_private_json(
        client.credentials_path,
        {
            "license_serial": "N0JCG-SCN-ABCD-EFGH-JKLM-2345",
            "email": "user@example.com",
        },
    )
    client._write_private_json(client.lease_path, {"lease": {}, "signature": "old"})
    monkeypatch.setattr(
        client,
        "_request_validation",
        Mock(side_effect=backend.LicenseError("license revoked")),
    )

    status = client.refresh()

    assert status["registered"] is False
    assert status["validation_error"] == "license revoked"
    assert not client.lease_path.exists()


def test_service_unavailable_response_is_treated_as_offline(tmp_path: Path) -> None:
    error = urllib.error.HTTPError(
        url="https://www.n0jcg.com/api/v1/licenses/validate",
        code=503,
        msg="Service Unavailable",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"licensing service is not configured"}'),
    )
    client = LicenseClient(
        product_slug="scanner",
        app_version="3.1.0",
        state_root=tmp_path,
        environment={"N0JCG_INSTALLATION_ID": "offline-appliance"},
        opener=Mock(side_effect=error),
    )

    with pytest.raises(ConnectionError, match="license server unavailable"):
        client._request_validation(
            {
                "license_serial": "N0JCG-SCN-ABCD-EFGH-JKLM-2345",
                "email": "user@example.com",
            }
        )


def test_rsa_sha256_lease_signature_verification(tmp_path: Path) -> None:
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("OpenSSL is unavailable")
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    message_path = tmp_path / "lease.json"
    signature_path = tmp_path / "lease.sig"
    message = b'{"signed":"n0jcg-license-test"}'
    message_path.write_bytes(message)
    subprocess.run(
        [openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:1024", "-out", str(private_key)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [openssl, "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [openssl, "dgst", "-sha256", "-sign", str(private_key), "-out", str(signature_path), str(message_path)],
        check=True,
        capture_output=True,
    )
    modulus_output = subprocess.run(
        [openssl, "rsa", "-pubin", "-in", str(public_key), "-modulus", "-noout"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    modulus_hex = modulus_output.split("=", 1)[1]
    signature = signature_path.read_bytes()

    assert verify_rsa_sha256_signature(
        message,
        signature,
        modulus_hex=modulus_hex,
        exponent=65537,
    )
    assert not verify_rsa_sha256_signature(
        message + b"tampered",
        signature,
        modulus_hex=modulus_hex,
        exponent=65537,
    )


class FakeTimer:
    instances: list["FakeTimer"] = []

    def __init__(self, interval, function, args=()) -> None:
        self.interval = interval
        self.function = function
        self.args = args
        self.daemon = False
        self.started = False
        self.cancelled = False
        self.instances.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


def _manager_for_trial(registered: bool) -> ScannerManager:
    manager = ScannerManager.__new__(ScannerManager)
    manager.status = ScannerStatus()
    manager.process = None
    manager.lock = threading.RLock()
    manager.control_lock = threading.Lock()
    manager._trial_timer = None
    manager._trial_generation = 0
    manager._trial_started_epoch = None
    manager._trial_deadline_epoch = None
    manager._trial_expired = False
    manager._timer_factory = FakeTimer
    manager._registration_provider = lambda _root: {
        "serial_number": "N0JCG-1111-2222-3333-4444",
        "registered": registered,
        "mode": "registered" if registered else "trial",
        "trial_limit_seconds": None if registered else 300,
        "registration_file": "/private/registration.env",
        "registration_file_present": registered,
    }
    return manager


def test_unregistered_trial_arms_once_without_resetting_countdown() -> None:
    FakeTimer.instances.clear()
    manager = _manager_for_trial(False)

    with manager.lock:
        manager._arm_trial_limit_locked()
        first_deadline = manager._trial_deadline_epoch
        manager._arm_trial_limit_locked()

    assert len(FakeTimer.instances) == 1
    assert FakeTimer.instances[0].interval == 300
    assert FakeTimer.instances[0].started is True
    assert manager._trial_deadline_epoch == first_deadline
    assert manager.status.registration["trial_active"] is True


def test_registered_installation_does_not_arm_trial_timer() -> None:
    FakeTimer.instances.clear()
    manager = _manager_for_trial(True)

    with manager.lock:
        manager._arm_trial_limit_locked()

    assert FakeTimer.instances == []
    assert manager._trial_deadline_epoch is None
    assert manager.status.registration["registered"] is True


def test_backend_activation_cancels_trial_without_returning_credentials() -> None:
    FakeTimer.instances.clear()
    manager = _manager_for_trial(False)
    del manager._registration_provider
    client = Mock()
    client.status.return_value = {
        "serial_number": "N0JCG-1111-2222-3333-4444",
        "registered": True,
        "mode": "registered",
        "license_suffix": "JKLM-2345",
        "trial_limit_seconds": None,
    }
    client.activate.return_value = dict(client.status.return_value)
    manager.license_client = client
    manager._trial_timer = Mock()

    result = manager.activate_license(
        {
            "license_serial": "N0JCG-SCN-ABCD-EFGH-JKLM-2345",
            "email": "user@example.com",
        }
    )

    client.activate.assert_called_once_with(
        "N0JCG-SCN-ABCD-EFGH-JKLM-2345",
        "user@example.com",
    )
    assert result["registration"]["registered"] is True
    assert "email" not in json.dumps(result).lower()
    assert "N0JCG-SCN-ABCD-EFGH-JKLM-2345" not in json.dumps(result)


def test_trial_expiry_stops_all_scanners(monkeypatch) -> None:
    FakeTimer.instances.clear()
    manager = _manager_for_trial(False)
    process = Mock()
    process.pid = 4321
    process.poll.return_value = None
    process.wait.return_value = 0
    manager.process = process
    manager.analog_service_controller = Mock()
    manager.analog_service_controller.stop.return_value = {
        "vhf": "inactive",
        "uhf": "inactive",
    }
    manager.activity_tracker = Mock()
    manager.activity_tracker.reset.return_value = {"distinct_voice_calls": 0}
    manager.status_payload = lambda: asdict(manager.status)
    monkeypatch.setattr(backend, "_reset_analog_dashboard_lock_counters", lambda: [])

    with manager.lock:
        manager._arm_trial_limit_locked()
    timer = FakeTimer.instances[0]
    timer.function(*timer.args)

    process.terminate.assert_called_once_with()
    manager.analog_service_controller.stop.assert_called_once_with()
    assert manager.status.scanner_state == "trial_expired"
    assert manager.status.coordinated_scanners == {
        "p25": "stopped",
        "vhf": "stopped",
        "uhf": "stopped",
    }
    assert manager.status.registration["trial_expired"] is True
    assert "five-minute trial ended" in manager.status.last_event


def test_registration_badges_exist_in_both_interfaces() -> None:
    root = Path(__file__).resolve().parents[1]
    desktop_html = (root / "web/index.html").read_text(encoding="utf-8")
    desktop_js = (root / "web/app.js").read_text(encoding="utf-8")
    mobile_html = (root / "web/mobile.html").read_text(encoding="utf-8")
    mobile_js = (root / "web/mobile.js").read_text(encoding="utf-8")

    assert 'id="registrationBadge"' in desktop_html
    assert 'id="registrationSerial"' in desktop_html
    assert 'id="licenseSerialInput"' in desktop_html
    assert 'id="licenseEmailInput"' in desktop_html
    assert "/api/license/activate" in desktop_js
    assert "TRIAL ENDED" in desktop_js
    assert 'id="registrationBadge"' in mobile_html
    assert 'id="registrationSerial"' in mobile_html
    assert 'id="licenseSerialInput"' in mobile_html
    assert 'id="licenseEmailInput"' in mobile_html
    assert "/api/license/activate" in mobile_js
    assert "Trial ended" in mobile_js
