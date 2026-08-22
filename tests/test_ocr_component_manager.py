"""Network-isolated tests for optional Paddle component installation."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

import paddle_component_worker
from services.ocr.component_manager import (
    ComponentCancelledError,
    ComponentInstallError,
    ComponentVerificationError,
    PaddleComponentManager,
    PaddleComponentManifest,
)


class Response:
    def __init__(self, payload: bytes):
        self.stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}
    def read(self, size=-1): return self.stream.read(size)
    def close(self): pass


def component_catalog(files: dict[str, bytes]) -> bytes:
    records = [
        {
            "path": path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for path, content in sorted(files.items())
    ]
    return json.dumps(
        {"schema_version": 1, "files": records},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def component_zip(
    files: dict[str, bytes] | None = None,
    *,
    catalog_files: dict[str, bytes] | None = None,
    catalog_signature: bytes = b"catalog-cms",
) -> bytes:
    payload_files = files or {
        "PaddleOCRComponent.exe": b"worker",
        "runtime/data.bin": b"runtime",
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        for path, content in payload_files.items():
            package.writestr(path, content)
        package.writestr("component-files.json", component_catalog(catalog_files or payload_files))
        package.writestr("component-files.p7s", catalog_signature)
    return stream.getvalue()


def manager(tmp_path: Path, package: bytes) -> PaddleComponentManager:
    return PaddleComponentManager(
        tmp_path / "components",
        opener=lambda request, timeout: Response(package),
        signature_verifier=lambda _path: None,
        manifest_signature_verifier=lambda _content, _signature: None,
    )


def manifest(package: bytes) -> PaddleComponentManifest:
    return PaddleComponentManifest.from_mapping({"schema_version": 1, "version": "1.0.0", "url": "https://example.test/component.zip", "sha256": hashlib.sha256(package).hexdigest(), "size": len(package), "entrypoint": "PaddleOCRComponent.exe"})


def test_verified_download_reports_progress_and_activates_atomically(tmp_path: Path):
    package = component_zip(); events = []
    subject = manager(tmp_path, package)
    entrypoint = subject.ensure_installed(manifest(package), progress_callback=lambda done, total: events.append((done, total)))
    assert entrypoint.read_bytes() == b"worker"
    assert events[0] == (0, len(package)) and events[-1] == (len(package), len(package))
    assert subject.is_installed(manifest(package))
    assert not list((tmp_path / "components").glob("*.partial"))


def test_checksum_failure_never_replaces_existing_component(tmp_path: Path):
    good = component_zip(); subject = manager(tmp_path, good); subject.ensure_installed(manifest(good))
    bad = PaddleComponentManifest.from_mapping({**manifest(good).__dict__, "version": "2.0.0", "sha256": "0" * 64})
    with pytest.raises(ComponentVerificationError): subject.ensure_installed(bad)
    assert subject.installed_manifest().version == "1.0.0"


@pytest.mark.parametrize("name", ["../escape.exe", "/absolute.exe", "folder\\escape.exe"])
def test_zip_slip_paths_are_rejected(tmp_path: Path, name: str):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package: package.writestr(name, b"bad")
    payload = stream.getvalue(); subject = manager(tmp_path, payload)
    data = {"schema_version": 1, "version": "1", "url": "https://example.test/a.zip", "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload), "entrypoint": "PaddleOCRComponent.exe"}
    # Keep a valid requested entrypoint so extraction, rather than manifest parsing, is tested.
    with pytest.raises(ComponentInstallError): subject.ensure_installed(data)
    assert not subject.active_root.exists()


def test_zip_symlink_is_rejected(tmp_path: Path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        info = zipfile.ZipInfo("PaddleOCRComponent.exe"); info.external_attr = (stat.S_IFLNK | 0o777) << 16
        package.writestr(info, "outside")
    payload = stream.getvalue(); subject = manager(tmp_path, payload)
    with pytest.raises(ComponentInstallError): subject.ensure_installed(manifest(payload))


def test_cancelled_download_leaves_no_partial_file(tmp_path: Path):
    payload = component_zip(); subject = manager(tmp_path, payload)
    with pytest.raises(ComponentCancelledError): subject.ensure_installed(manifest(payload), cancel_check=lambda: True)
    assert not subject.active_root.exists()


def test_invalid_worker_signature_never_activates_component(tmp_path: Path):
    payload = component_zip()

    def reject(_path):
        raise RuntimeError("untrusted publisher")

    subject = PaddleComponentManager(
        tmp_path / "components",
        opener=lambda request, timeout: Response(payload),
        signature_verifier=reject,
        manifest_signature_verifier=lambda _content, _signature: None,
    )

    with pytest.raises(ComponentVerificationError, match="数字签名"):
        subject.ensure_installed(manifest(payload))
    assert not subject.active_root.exists()


def test_component_entrypoint_is_reverified_after_local_replacement(tmp_path: Path):
    payload = component_zip()
    calls = []

    def verify(path):
        calls.append(path.read_bytes())
        if path.read_bytes() != b"worker":
            raise RuntimeError("replacement is not trusted")

    subject = PaddleComponentManager(
        tmp_path / "components",
        opener=lambda request, timeout: Response(payload),
        signature_verifier=verify,
        manifest_signature_verifier=lambda _content, _signature: None,
    )
    entrypoint = subject.ensure_installed(manifest(payload))
    calls.clear()

    assert subject.component_entrypoint() == entrypoint
    assert calls == []
    entrypoint.write_bytes(b"replaced-worker")
    with pytest.raises(ComponentVerificationError, match="数字签名"):
        subject.component_entrypoint()
    assert calls == [b"replaced-worker"]


def test_catalog_signature_and_full_tree_are_verified_before_activation(tmp_path: Path):
    payload = component_zip()
    verified = []
    subject = PaddleComponentManager(
        tmp_path / "components",
        opener=lambda request, timeout: Response(payload),
        signature_verifier=lambda _path: None,
        manifest_signature_verifier=lambda content, signature: verified.append((content, signature)),
    )

    subject.ensure_installed(manifest(payload))

    assert len(verified) == 1
    assert json.loads(verified[0][0])["schema_version"] == 1
    assert verified[0][1] == b"catalog-cms"


def test_catalog_rejects_modified_internal_file_before_activation(tmp_path: Path):
    expected = {
        "PaddleOCRComponent.exe": b"worker",
        "_internal/runtime.dll": b"trusted-runtime",
    }
    modified = {**expected, "_internal/runtime.dll": b"tainted-runtime"}
    payload = component_zip(modified, catalog_files=expected)
    subject = manager(tmp_path, payload)

    with pytest.raises(ComponentVerificationError, match="SHA-256"):
        subject.ensure_installed(manifest(payload))
    assert not subject.active_root.exists()


def test_catalog_rejects_unlisted_extra_file_before_activation(tmp_path: Path):
    expected = {"PaddleOCRComponent.exe": b"worker"}
    payload = component_zip({**expected, "_internal/extra.pyd": b"extra"}, catalog_files=expected)
    subject = manager(tmp_path, payload)

    with pytest.raises(ComponentVerificationError, match="文件集合"):
        subject.ensure_installed(manifest(payload))
    assert not subject.active_root.exists()


def test_fresh_manager_rejects_locally_replaced_internal_file(tmp_path: Path):
    payload = component_zip()
    subject = manager(tmp_path, payload)
    subject.ensure_installed(manifest(payload))
    (subject.active_root / "runtime" / "data.bin").write_bytes(b"local-replacement")

    fresh_manager = manager(tmp_path, payload)
    with pytest.raises(ComponentVerificationError, match="大小|SHA-256"):
        fresh_manager.component_entrypoint()


def test_missing_signed_catalog_never_activates_component(tmp_path: Path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("PaddleOCRComponent.exe", b"worker")
    payload = stream.getvalue()
    subject = manager(tmp_path, payload)

    with pytest.raises(ComponentVerificationError, match="缺少组件文件目录"):
        subject.ensure_installed(manifest(payload))
    assert not subject.active_root.exists()


def test_manifest_rejects_non_executable_or_windows_drive_entrypoint():
    package = component_zip()
    data = {"schema_version": 1, "version": "1", "url": "https://example.test/a.zip", "sha256": hashlib.sha256(package).hexdigest(), "size": len(package)}
    with pytest.raises(ComponentVerificationError):
        PaddleComponentManifest.from_mapping({**data, "entrypoint": "worker.py"})
    with pytest.raises(ComponentVerificationError):
        PaddleComponentManifest.from_mapping({**data, "entrypoint": "C:/worker.exe"})


def test_manifest_rejects_unknown_protocol_version():
    with pytest.raises(ComponentVerificationError, match="manifest"):
        PaddleComponentManifest.from_mapping(
            {
                "schema_version": 2,
                "version": "1.0.0",
                "url": "https://example.test/component.zip",
                "sha256": "0" * 64,
                "size": 1,
                "entrypoint": "PaddleOCRComponent.exe",
            }
        )


def test_fetched_manifest_is_verified_before_json_parsing(tmp_path: Path):
    raw_manifest = json.dumps(manifest(component_zip()).__dict__).encode("utf-8")
    detached_signature = b"signed-cms"
    requested_urls = []
    verified = []

    def opener(request, timeout):
        requested_urls.append(request.full_url)
        if request.full_url.endswith("manifest.json"):
            return Response(raw_manifest)
        if request.full_url.endswith("manifest.p7s"):
            return Response(detached_signature)
        raise AssertionError(f"unexpected URL: {request.full_url}")

    subject = PaddleComponentManager(
        tmp_path / "components",
        manifest_url="https://example.test/releases/manifest.json",
        opener=opener,
        signature_verifier=lambda _path: None,
        manifest_signature_verifier=lambda content, signature: verified.append((content, signature)),
    )

    resolved = subject.fetch_manifest()

    assert resolved.version == "1.0.0"
    assert requested_urls == [
        "https://example.test/releases/manifest.json",
        "https://example.test/releases/manifest.p7s",
    ]
    assert verified == [(raw_manifest, detached_signature)]


def test_untrusted_manifest_is_rejected_before_invalid_json_is_parsed(tmp_path: Path):
    def opener(request, timeout):
        return Response(b"not-json" if request.full_url.endswith(".json") else b"signature")

    def reject(_content, _signature):
        raise RuntimeError("untrusted publisher")

    subject = PaddleComponentManager(
        tmp_path / "components",
        manifest_url="https://example.test/manifest.json",
        opener=opener,
        signature_verifier=lambda _path: None,
        manifest_signature_verifier=reject,
    )

    with pytest.raises(ComponentVerificationError, match="签名验证失败"):
        subject.fetch_manifest()


def test_worker_sets_component_owned_paddlex_cache(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(paddle_component_worker.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv("PADDLE_PDX_CACHE_HOME", raising=False)
    cache = paddle_component_worker.configure_paddlex_cache()
    assert cache == tmp_path / "models"
    assert cache.is_dir()
    assert paddle_component_worker.os.environ["PADDLE_PDX_CACHE_HOME"] == str(cache)
