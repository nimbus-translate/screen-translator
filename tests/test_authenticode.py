"""Authenticode verification boundary tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.authenticode as authenticode
from services.authenticode import (
    AuthenticodeVerificationError,
    DetachedSignatureVerificationError,
)


def _prepare_windows(monkeypatch, tmp_path):
    system_root = tmp_path / "Windows"
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"powershell")
    target = tmp_path / "candidate.exe"
    target.write_bytes(b"candidate")
    monkeypatch.setattr(authenticode.os, "name", "nt")
    monkeypatch.setenv("SystemRoot", str(system_root))
    return target, powershell


def test_verifier_uses_scoped_paths_and_hidden_powershell(monkeypatch, tmp_path):
    target, powershell = _prepare_windows(monkeypatch, tmp_path)
    reference = tmp_path / "running.exe"
    reference.write_bytes(b"running")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(authenticode.subprocess, "run", run)
    monkeypatch.setattr(authenticode.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    authenticode.verify_authenticode(target, reference_path=reference)

    command, kwargs = calls[0]
    assert command[0] == str(powershell)
    assert str(target) not in " ".join(command)
    assert kwargs["env"]["SCREEN_TRANSLATOR_VERIFY_TARGET"] == str(target.resolve())
    assert kwargs["env"]["SCREEN_TRANSLATOR_VERIFY_REFERENCE"] == str(reference.resolve())
    assert kwargs["creationflags"] == 0x08000000


def test_verifier_rejects_untrusted_signature(monkeypatch, tmp_path):
    target, _powershell = _prepare_windows(monkeypatch, tmp_path)
    monkeypatch.setattr(
        authenticode.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=10,
            stdout="",
            stderr="Target Authenticode status: NotSigned",
        ),
    )

    with pytest.raises(AuthenticodeVerificationError, match="NotSigned"):
        authenticode.verify_authenticode(target)


def test_detached_cms_verifier_uses_private_files_and_reference(monkeypatch, tmp_path):
    _target, powershell = _prepare_windows(monkeypatch, tmp_path)
    reference = tmp_path / "running.exe"
    reference.write_bytes(b"running")
    calls = []

    def run(command, **kwargs):
        environment = kwargs["env"]
        content_path = type(tmp_path)(environment["SCREEN_TRANSLATOR_VERIFY_CONTENT"])
        signature_path = type(tmp_path)(environment["SCREEN_TRANSLATOR_VERIFY_SIGNATURE"])
        calls.append((command, kwargs, content_path, signature_path))
        assert content_path.read_bytes() == b'{"version":"0.2.5"}'
        assert signature_path.read_bytes() == b"detached-cms"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(authenticode.subprocess, "run", run)
    monkeypatch.setattr(authenticode.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    authenticode.verify_detached_cms(
        b'{"version":"0.2.5"}',
        b"detached-cms",
        reference_path=reference,
    )

    command, kwargs, content_path, signature_path = calls[0]
    assert command[0] == str(powershell)
    assert str(content_path) not in " ".join(command)
    assert str(signature_path) not in " ".join(command)
    assert kwargs["env"]["SCREEN_TRANSLATOR_VERIFY_REFERENCE"] == str(reference.resolve())
    assert kwargs["creationflags"] == 0x08000000
    assert not content_path.exists()
    assert not signature_path.exists()


def test_detached_cms_verifier_rejects_untrusted_signer(monkeypatch, tmp_path):
    _target, _powershell = _prepare_windows(monkeypatch, tmp_path)
    monkeypatch.setattr(
        authenticode.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=25,
            stdout="",
            stderr="Detached CMS publisher does not match the running application",
        ),
    )

    with pytest.raises(DetachedSignatureVerificationError, match="does not match"):
        authenticode.verify_detached_cms(b"manifest", b"signature")
