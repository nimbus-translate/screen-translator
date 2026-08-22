"""Network-isolated tests for the release update service."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from services.update_service import (
    ChecksumMismatchError,
    ChecksumUnavailableError,
    ReleaseNotFoundError,
    SemanticVersion,
    UnsafeUpdatePathError,
    UpdateAsset,
    UpdateAssetNotFoundError,
    UpdateCancelledError,
    UpdateNetworkError,
    UpdateService,
)


LATEST_URL = "https://api.github.com/repos/nimbus-translate/screen-translator/releases/latest"
RELEASES_URL = "https://api.github.com/repos/nimbus-translate/screen-translator/releases?per_page=100"


class FakeResponse:
    def __init__(self, body: bytes, headers=None):
        self._stream = io.BytesIO(body)
        self.headers = headers or {}
        self.closed = False

    def read(self, amount=-1):
        return self._stream.read(amount)

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, replies):
        self.replies = replies
        self.urls = []

    def __call__(self, request, timeout):
        url = request.full_url
        self.urls.append(url)
        reply = self.replies[url]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            return FakeResponse(*reply)
        return FakeResponse(reply)


def _release(assets, tag="v0.2.0", **overrides):
    release = {
        "tag_name": tag,
        "name": tag,
        "html_url": "https://github.com/nimbus/release",
        "draft": False,
        "prerelease": "-" in tag,
        "assets": assets,
    }
    release.update(overrides)
    return release


def _asset(name="ScreenTranslator-0.2.0-windows-lite-setup.exe"):
    return {"name": name, "browser_download_url": "https://download.example/installer.exe", "size": 9}


def _service(release, replies, *, releases=None):
    routes = {LATEST_URL: json.dumps(release).encode(), **replies}
    if releases is not None:
        routes[RELEASES_URL] = json.dumps(releases).encode()
    return UpdateService(opener=FakeOpener(routes))


def test_semantic_versions_follow_prerelease_order():
    assert SemanticVersion.parse("v1.2.0-alpha.2") < SemanticVersion.parse("1.2.0-alpha.10")
    assert SemanticVersion.parse("1.2.0-rc.1") < SemanticVersion.parse("1.2.0")
    assert SemanticVersion.parse("1.2") == SemanticVersion.parse("1.2.0+build.4")


def test_update_check_selects_windows_lightweight_installer():
    asset = _asset()
    release = _release([
        {"name": "ScreenTranslator-0.2.0-macos.dmg", "browser_download_url": "https://download.example/mac"},
        {"name": "ScreenTranslator-0.2.0-windows-setup.exe", "browser_download_url": "https://download.example/full"},
        asset,
        {"name": asset["name"] + ".sha256", "browser_download_url": "https://download.example/hash"},
    ])
    service = _service(release, {"https://download.example/hash": b"0" * 64})

    update = service.check_for_update("0.1.9")

    assert update is not None
    assert update.latest_version == SemanticVersion.parse("0.2.0")
    assert update.asset.name == asset["name"]
    assert service.check_for_update("0.2.0") is None


def test_prerelease_is_not_offered_unless_requested():
    release = _release([_asset()], "v0.2.0-rc.1")
    service = _service(release, {}, releases=[release])
    assert service.check_for_update("0.1.0") is None
    assert service.check_for_update("0.1.0", include_prereleases=True) is not None


def test_prerelease_check_uses_release_list_and_selects_highest_usable_semver():
    releases = [
        _release([_asset("ScreenTranslator-9.0.0-windows-lite.exe")], "v9.0.0", draft=True),
        _release([], "v8.0.0"),
        _release([_asset("ScreenTranslator-bad-windows-lite.exe")], "not-semver"),
        _release([_asset("ScreenTranslator-0.4.0-windows-lite.exe")], "v0.4.0"),
        _release([_asset("ScreenTranslator-0.5.0-rc.2-windows-lite.exe")], "v0.5.0-rc.2"),
        _release([_asset("ScreenTranslator-0.5.0-rc.10-windows-lite.exe")], "v0.5.0-rc.10"),
    ]
    opener = FakeOpener({RELEASES_URL: json.dumps(releases).encode()})
    service = UpdateService(opener=opener)

    update = service.check_for_update("0.3.0", include_prereleases=True)

    assert update is not None
    assert update.latest_version == SemanticVersion.parse("0.5.0-rc.10")
    assert update.asset.name == "ScreenTranslator-0.5.0-rc.10-windows-lite.exe"
    assert opener.urls == [RELEASES_URL]


def test_stable_check_keeps_latest_endpoint_fast_path():
    release = _release([_asset()], "v0.2.0")
    opener = FakeOpener({LATEST_URL: json.dumps(release).encode()})
    service = UpdateService(opener=opener)

    assert service.check_for_update("0.1.0") is not None
    assert opener.urls == [LATEST_URL]


@pytest.mark.parametrize(
    "unsafe_name",
    [
        ".",
        "..",
        "../ScreenTranslator-windows-lite.exe",
        r"..\ScreenTranslator-windows-lite.exe",
        "/ScreenTranslator-windows-lite.exe",
        r"C:\ScreenTranslator-windows-lite.exe",
        "C:ScreenTranslator-windows-lite.exe",
        "nested/ScreenTranslator-windows-lite.exe",
        r"nested\ScreenTranslator-windows-lite.exe",
    ],
)
def test_asset_selection_rejects_non_basename_paths(unsafe_name):
    with pytest.raises(UpdateAssetNotFoundError):
        UpdateService.select_windows_lightweight_asset([_asset(unsafe_name)])


def test_unsafe_lite_asset_cannot_outrank_safe_installer():
    selected = UpdateService.select_windows_lightweight_asset([
        _asset("../ScreenTranslator-windows-lite.exe"),
        _asset("ScreenTranslator-windows-setup.exe"),
    ])

    assert selected.name == "ScreenTranslator-windows-setup.exe"


def test_download_verifies_sidecar_hash_reports_progress_and_is_atomic(tmp_path: Path):
    payload = b"verified update bytes"
    digest = hashlib.sha256(payload).hexdigest()
    asset = _asset()
    hash_url = "https://download.example/hash"
    release = _release([asset, {"name": asset["name"] + ".sha256", "browser_download_url": hash_url}])
    service = _service(release, {hash_url: f"{digest}  {asset['name']}\n".encode(), asset["browser_download_url"]: (payload, {"Content-Length": str(len(payload))})})
    update = service.check_for_update("0.1.0")
    progress = []
    destination = tmp_path / "ScreenTranslator-setup.exe"

    verified = service.download_verified_update(
        update,
        destination,
        progress_callback=lambda done, total: progress.append((done, total)),
        chunk_size=4,
    )
    assert verified.path == destination
    assert verified.sha256 == digest
    assert destination.read_bytes() == payload
    assert progress[0] == (0, len(payload))
    assert progress[-1] == (len(payload), len(payload))
    assert not list(tmp_path.glob("*.partial"))


def test_download_rejects_destination_traversal_before_network_io(tmp_path: Path):
    asset = _asset()
    hash_url = "https://download.example/hash"
    release = _release([asset, {"name": asset["name"] + ".sha256", "browser_download_url": hash_url}])
    service = _service(release, {hash_url: b"0" * 64, asset["browser_download_url"]: b"ignored"})
    update = service.check_for_update("0.1.0")
    destination = tmp_path / "downloads" / ".." / "escaped.exe"

    with pytest.raises(UnsafeUpdatePathError):
        service.download_update(update, destination)

    assert not (tmp_path / "escaped.exe").exists()
    assert service._opener.urls == [LATEST_URL]


def test_download_revalidates_asset_name_if_update_info_is_forged(tmp_path: Path):
    asset = _asset()
    release = _release([asset])
    service = _service(release, {})
    update = service.check_for_update("0.1.0")
    forged = replace(
        update,
        asset=UpdateAsset("../escaped.exe", asset["browser_download_url"], asset["size"]),
    )

    with pytest.raises(UnsafeUpdatePathError):
        service.download_update(forged, tmp_path / "escaped.exe")

    assert not (tmp_path / "escaped.exe").exists()
    assert service._opener.urls == [LATEST_URL]


def test_manifest_checksum_is_used_when_sidecar_absent(tmp_path: Path):
    payload = b"manifest checked"
    digest = hashlib.sha256(payload).hexdigest()
    asset = _asset()
    manifest_url = "https://download.example/SHA256SUMS"
    release = _release([asset, {"name": "SHA256SUMS", "browser_download_url": manifest_url}])
    service = _service(release, {manifest_url: f"{digest} *{asset['name']}\n".encode(), asset["browser_download_url"]: payload})
    update = service.check_for_update("0.1.0")

    service.download_update(update, tmp_path / "setup.exe")
    assert (tmp_path / "setup.exe").read_bytes() == payload


def test_hash_mismatch_does_not_replace_existing_file_or_leave_partial(tmp_path: Path):
    asset = _asset()
    hash_url = "https://download.example/hash"
    release = _release([asset, {"name": asset["name"] + ".sha256", "browser_download_url": hash_url}])
    service = _service(release, {hash_url: b"0" * 64, asset["browser_download_url"]: b"untrusted bytes"})
    update = service.check_for_update("0.1.0")
    destination = tmp_path / "setup.exe"
    destination.write_bytes(b"existing good installer")

    with pytest.raises(ChecksumMismatchError):
        service.download_update(update, destination)
    assert destination.read_bytes() == b"existing good installer"
    assert not list(tmp_path.glob("*.partial"))


def test_cancel_and_missing_checksum_leave_no_file(tmp_path: Path):
    asset = _asset()
    release = _release([asset])
    service = _service(release, {asset["browser_download_url"]: b"ignored"})
    update = service.check_for_update("0.1.0")
    with pytest.raises(ChecksumUnavailableError):
        service.download_update(update, tmp_path / "setup.exe")
    assert not (tmp_path / "setup.exe").exists()

    release = _release([asset, {"name": asset["name"] + ".sha256", "browser_download_url": "https://download.example/hash"}])
    service = _service(release, {"https://download.example/hash": b"0" * 64, asset["browser_download_url"]: b"ignored"})
    update = service.check_for_update("0.1.0")
    with pytest.raises(UpdateCancelledError):
        service.download_update(update, tmp_path / "cancelled.exe", cancel_check=lambda: True)
    assert not (tmp_path / "cancelled.exe").exists()


def test_404_and_timeout_have_clear_exceptions():
    not_found = HTTPError(LATEST_URL, 404, "missing", None, None)
    with pytest.raises(ReleaseNotFoundError):
        UpdateService(opener=FakeOpener({LATEST_URL: not_found})).fetch_latest_release()
    with pytest.raises(UpdateNetworkError):
        UpdateService(opener=FakeOpener({LATEST_URL: URLError("timed out")})).fetch_latest_release()


def test_download_404_does_not_leave_a_partial_file(tmp_path: Path):
    payload = b"expected content"
    asset = _asset()
    hash_url = "https://download.example/hash"
    release = _release([asset, {"name": asset["name"] + ".sha256", "browser_download_url": hash_url}])
    missing = HTTPError(asset["browser_download_url"], 404, "missing", None, None)
    service = _service(release, {hash_url: hashlib.sha256(payload).hexdigest().encode(), asset["browser_download_url"]: missing})
    update = service.check_for_update("0.1.0")
    destination = tmp_path / "setup.exe"

    with pytest.raises(ReleaseNotFoundError):
        service.download_update(update, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob("*.partial"))
