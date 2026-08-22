"""Verified GitHub-release update downloads.

This module deliberately does not start installers.  UI code can ask it about an
update and download a verified package, then decide how and when to launch an
external updater.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from pathlib import PureWindowsPath
import re
import tempfile
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


GITHUB_RELEASE_URL = "https://api.github.com/repos/{repository}/releases/latest"
GITHUB_RELEASES_URL = "https://api.github.com/repos/{repository}/releases?per_page=100"
_CHECKSUM_MANIFEST_NAMES = {
    "sha256sums",
    "sha256sums.txt",
    "checksums.txt",
    "checksums",
    "manifest.json",
    "checksums.json",
    "release-manifest.json",
}
_SHA256_RE = re.compile(r"\b([a-fA-F0-9]{64})\b")


class UpdateServiceError(RuntimeError):
    """Base class for update-service failures."""


class UpdateNetworkError(UpdateServiceError):
    """The release server could not be reached or returned an HTTP error."""


class ReleaseNotFoundError(UpdateNetworkError):
    """The repository has no latest release."""


class UpdateCancelledError(UpdateServiceError):
    """A caller cancelled a pending update request or download."""


class UpdateAssetNotFoundError(UpdateServiceError):
    """The release has no usable Windows installer asset."""


class UnsafeUpdatePathError(UpdateServiceError):
    """A release asset or local destination could escape its intended directory."""


class ChecksumUnavailableError(UpdateServiceError):
    """The release does not publish a SHA-256 checksum for the selected asset."""


class ChecksumMismatchError(UpdateServiceError):
    """The downloaded bytes do not match the release checksum."""


@dataclass(frozen=True, order=False)
class SemanticVersion:
    """A small, dependency-free SemVer comparator (build metadata is ignored)."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        text = str(value).strip()
        if text.lower().startswith("v"):
            text = text[1:]
        text = text.split("+", 1)[0]
        match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?", text)
        if not match:
            raise ValueError(f"不是有效的语义版本: {value!r}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3) or 0), prerelease)

    def _compare(self, other: "SemanticVersion") -> int:
        core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if core != other_core:
            return -1 if core < other_core else 1
        if not self.prerelease or not other.prerelease:
            if self.prerelease == other.prerelease:
                return 0
            return -1 if self.prerelease else 1
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            left_numeric, right_numeric = left.isdecimal(), right.isdecimal()
            if left_numeric and right_numeric:
                return -1 if int(left) < int(right) else 1
            if left_numeric != right_numeric:
                return -1 if left_numeric else 1
            return -1 if left < right else 1
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return -1 if len(self.prerelease) < len(other.prerelease) else 1

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._compare(other) < 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SemanticVersion) and self._compare(other) == 0

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if not self.prerelease else base + "-" + ".".join(self.prerelease)


@dataclass(frozen=True)
class UpdateAsset:
    name: str
    url: str
    size: int | None = None


@dataclass(frozen=True)
class VerifiedDownload:
    path: Path
    sha256: str


@dataclass(frozen=True)
class UpdateInfo:
    current_version: SemanticVersion
    latest_version: SemanticVersion
    release_name: str
    release_url: str
    asset: UpdateAsset
    release: Mapping[str, Any]

    @property
    def is_available(self) -> bool:
        return self.current_version < self.latest_version


ProgressCallback = Callable[[int, int | None], None]
CancelCheck = Callable[[], bool]
Opener = Callable[..., Any]


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a release artifact without loading the installer into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while block := stream.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


class UpdateService:
    """Read GitHub releases and download a checksum-verified Windows package."""

    def __init__(
        self,
        repository: str = "nimbus-translate/screen-translator",
        *,
        timeout_seconds: float = 15.0,
        opener: Opener | None = None,
        user_agent: str = "ScreenTranslator-Updater/1.0",
    ) -> None:
        if "/" not in repository or repository.startswith("/") or repository.endswith("/"):
            raise ValueError("repository 必须是 'owner/name' 形式")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.repository = repository
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urlopen
        self._user_agent = user_agent

    def fetch_latest_release(self, cancel_check: CancelCheck | Any | None = None) -> Mapping[str, Any]:
        """Fetch release metadata from GitHub's ``releases/latest`` endpoint."""
        url = GITHUB_RELEASE_URL.format(repository=self.repository)
        raw = self._read_all(url, cancel_check=cancel_check)
        try:
            release = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateNetworkError("GitHub 返回了无效的 release 元数据") from exc
        if not isinstance(release, dict):
            raise UpdateNetworkError("GitHub 返回的 release 元数据格式不正确")
        return release

    def fetch_releases(self, cancel_check: CancelCheck | Any | None = None) -> Sequence[Mapping[str, Any]]:
        """Fetch up to 100 releases, including prereleases, from GitHub."""
        url = GITHUB_RELEASES_URL.format(repository=self.repository)
        raw = self._read_all(url, cancel_check=cancel_check)
        try:
            releases = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateNetworkError("GitHub 返回了无效的 releases 元数据") from exc
        if not isinstance(releases, list) or any(not isinstance(item, Mapping) for item in releases):
            raise UpdateNetworkError("GitHub 返回的 releases 元数据格式不正确")
        return releases

    def check_for_update(
        self,
        current_version: str | SemanticVersion,
        *,
        include_prereleases: bool = False,
        cancel_check: CancelCheck | Any | None = None,
    ) -> UpdateInfo | None:
        """Return a selected lightweight Windows update, or ``None`` if current."""
        current = current_version if isinstance(current_version, SemanticVersion) else SemanticVersion.parse(current_version)
        if include_prereleases:
            selected = self._select_highest_usable_release(
                self.fetch_releases(cancel_check=cancel_check)
            )
            if selected is None:
                return None
            release, latest, asset = selected
        else:
            release = self.fetch_latest_release(cancel_check=cancel_check)
            if release.get("draft"):
                return None
            tag = release.get("tag_name")
            if not isinstance(tag, str):
                raise UpdateNetworkError("release 缺少 tag_name")
            latest = SemanticVersion.parse(tag)
            # Do not trust metadata alone: mirrors and hand-authored test feeds
            # can omit GitHub's flag while publishing a prerelease SemVer tag.
            if release.get("prerelease") or latest.prerelease:
                return None
            asset = self.select_windows_lightweight_asset(release.get("assets", ()))
        if not current < latest:
            return None
        tag = str(release.get("tag_name") or latest)
        release_url = str(release.get("html_url") or "")
        return UpdateInfo(current, latest, str(release.get("name") or tag), release_url, asset, release)

    @classmethod
    def _select_highest_usable_release(
        cls,
        releases: Sequence[Mapping[str, Any]],
    ) -> tuple[Mapping[str, Any], SemanticVersion, UpdateAsset] | None:
        """Choose the highest non-draft SemVer release that has a safe installer."""
        candidates: list[tuple[SemanticVersion, Mapping[str, Any], UpdateAsset]] = []
        for release in releases:
            if release.get("draft"):
                continue
            tag = release.get("tag_name")
            if not isinstance(tag, str):
                continue
            try:
                version = SemanticVersion.parse(tag)
                asset = cls.select_windows_lightweight_asset(release.get("assets", ()))
            except (ValueError, UpdateAssetNotFoundError):
                continue
            candidates.append((version, release, asset))
        if not candidates:
            return None
        version, release, asset = max(candidates, key=lambda item: item[0])
        return release, version, asset

    @staticmethod
    def select_windows_lightweight_asset(assets: Sequence[Mapping[str, Any]]) -> UpdateAsset:
        """Select a Windows installer, preferring explicitly light/lite builds."""
        if not isinstance(assets, Sequence) or isinstance(assets, (str, bytes)):
            raise UpdateAssetNotFoundError("release 中没有可用的 Windows 安装包")
        candidates: list[tuple[tuple[int, int, int, str], UpdateAsset]] = []
        for raw in assets:
            if not isinstance(raw, Mapping):
                continue
            name = raw.get("name")
            url = raw.get("browser_download_url")
            if not isinstance(name, str) or not isinstance(url, str):
                continue
            if not UpdateService._is_safe_asset_name(name):
                continue
            lower = name.casefold()
            if lower.endswith((".sha256", ".sha512", ".sig", ".asc")) or lower in _CHECKSUM_MANIFEST_NAMES:
                continue
            if not lower.endswith((".exe", ".msi")):
                continue
            # .exe/.msi are inherently Windows.  Reject clearly non-Windows
            # cross-platform names, while keeping generic installer names usable.
            if any(token in lower for token in ("macos", "darwin", "linux", "appimage")):
                continue
            light = 0 if any(token in lower for token in ("light", "lite", "slim", "windows-ocr")) else 1
            named_windows = 0 if any(token in lower for token in ("windows", "win32", "win64", "win-")) else 1
            installer = 0 if any(token in lower for token in ("setup", "installer")) else 1
            size_value = raw.get("size")
            size = int(size_value) if isinstance(size_value, int) and size_value >= 0 else None
            candidates.append(((light, named_windows, installer, lower), UpdateAsset(name, url, size)))
        if not candidates:
            raise UpdateAssetNotFoundError("release 中没有可用的 Windows 安装包")
        return min(candidates, key=lambda item: item[0])[1]

    @staticmethod
    def _is_safe_asset_name(name: str) -> bool:
        """Return whether a remote asset is one plain Windows filename."""
        if not name or name in {".", ".."} or "\x00" in name:
            return False
        if "/" in name or "\\" in name or ":" in name:
            return False
        windows_path = PureWindowsPath(name)
        return not windows_path.is_absolute() and not windows_path.drive and windows_path.name == name

    @classmethod
    def _validated_download_target(
        cls,
        asset: UpdateAsset,
        destination: str | os.PathLike[str],
    ) -> Path:
        """Resolve one local target without accepting traversal or symlink escapes."""
        if not cls._is_safe_asset_name(asset.name):
            raise UnsafeUpdatePathError("更新资源文件名不安全")
        target_input = Path(destination)
        if not target_input.name or target_input.name in {".", ".."}:
            raise UnsafeUpdatePathError("更新下载目标不是有效文件")
        if any(part == ".." for part in target_input.parts):
            raise UnsafeUpdatePathError("更新下载目标包含路径穿越")
        if target_input.drive and not target_input.is_absolute():
            raise UnsafeUpdatePathError("更新下载目标不能使用驱动器相对路径")

        target_input.parent.mkdir(parents=True, exist_ok=True)
        destination_root = target_input.parent.resolve()
        target = destination_root / target_input.name
        resolved_target = target.resolve(strict=False)
        if target.is_symlink() or resolved_target.parent != destination_root:
            raise UnsafeUpdatePathError("更新下载目标逃逸了目标目录")
        return target

    def download_update(
        self,
        update: UpdateInfo,
        destination: str | os.PathLike[str],
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_check: CancelCheck | Any | None = None,
        chunk_size: int = 256 * 1024,
    ) -> Path:
        """Download a verified update and return its path."""
        return self.download_verified_update(
            update,
            destination,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            chunk_size=chunk_size,
        ).path

    def download_verified_update(
        self,
        update: UpdateInfo,
        destination: str | os.PathLike[str],
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_check: CancelCheck | Any | None = None,
        chunk_size: int = 256 * 1024,
    ) -> VerifiedDownload:
        """Download and verify an update atomically. It never executes the file."""
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")
        self._raise_if_cancelled(cancel_check)
        target = self._validated_download_target(update.asset, destination)
        expected_hash = self._find_checksum(update.asset, update.release, cancel_check)
        fd, partial_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".partial", dir=target.parent)
        partial = Path(partial_name)
        downloaded = 0
        hasher = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as stream:
                response = self._open(update.asset.url)
                try:
                    total = self._content_length(response, update.asset.size)
                    if progress_callback:
                        progress_callback(0, total)
                    while True:
                        self._raise_if_cancelled(cancel_check)
                        block = response.read(chunk_size)
                        if not block:
                            break
                        stream.write(block)
                        hasher.update(block)
                        downloaded += len(block)
                        if progress_callback:
                            progress_callback(downloaded, total)
                    stream.flush()
                    os.fsync(stream.fileno())
                finally:
                    self._close(response)
            actual_hash = hasher.hexdigest()
            if actual_hash.casefold() != expected_hash.casefold():
                raise ChecksumMismatchError("安装包 SHA-256 校验失败，文件已丢弃")
            self._raise_if_cancelled(cancel_check)
            os.replace(partial, target)
            return VerifiedDownload(target, expected_hash.lower())
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def _find_checksum(
        self,
        asset: UpdateAsset,
        release: Mapping[str, Any],
        cancel_check: CancelCheck | Any | None,
    ) -> str:
        assets = release.get("assets", ())
        if not isinstance(assets, Sequence) or isinstance(assets, (str, bytes)):
            assets = ()
        sidecar = next(
            (
                item for item in assets
                if isinstance(item, Mapping)
                and str(item.get("name", "")).casefold() == f"{asset.name}.sha256".casefold()
                and isinstance(item.get("browser_download_url"), str)
            ),
            None,
        )
        if sidecar is not None:
            try:
                checksum = self._parse_checksum_document(
                    self._read_all(str(sidecar["browser_download_url"]), cancel_check=cancel_check), asset.name
                )
                if checksum:
                    return checksum
            except UpdateNetworkError as exc:
                # A broken sidecar must not silently prevent a valid release
                # manifest from being used.
                if not isinstance(exc, ReleaseNotFoundError):
                    raise

        for item in assets:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name", "")).casefold()
            url = item.get("browser_download_url")
            if name not in _CHECKSUM_MANIFEST_NAMES or not isinstance(url, str):
                continue
            checksum = self._parse_checksum_document(self._read_all(url, cancel_check=cancel_check), asset.name)
            if checksum:
                return checksum
        raise ChecksumUnavailableError(f"release 未提供 {asset.name} 的 SHA-256")

    @staticmethod
    def _parse_checksum_document(payload: bytes, asset_name: str) -> str | None:
        text = payload.decode("utf-8", errors="replace").strip()
        whole = _SHA256_RE.fullmatch(text)
        if whole:
            return whole.group(1).lower()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, Mapping):
            direct = data.get(asset_name)
            if isinstance(direct, str) and _SHA256_RE.fullmatch(direct.strip()):
                return direct.strip().lower()
            for container_key in ("assets", "files", "checksums"):
                entries = data.get(container_key)
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, Mapping) and str(entry.get("name") or entry.get("file")) == asset_name:
                            value = entry.get("sha256") or entry.get("hash")
                            if isinstance(value, str) and _SHA256_RE.fullmatch(value.strip()):
                                return value.strip().lower()
        escaped_name = re.escape(asset_name)
        match = re.search(rf"\b([a-fA-F0-9]{{64}})\b\s+\*?{escaped_name}(?:\s|$)", text, re.MULTILINE)
        return match.group(1).lower() if match else None

    def _read_all(self, url: str, *, cancel_check: CancelCheck | Any | None) -> bytes:
        self._raise_if_cancelled(cancel_check)
        response = self._open(url)
        try:
            chunks: list[bytes] = []
            while True:
                self._raise_if_cancelled(cancel_check)
                chunk = response.read(64 * 1024)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            self._close(response)

    def _open(self, url: str) -> Any:
        request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": self._user_agent})
        try:
            return self._opener(request, timeout=self.timeout_seconds)
        except HTTPError as exc:
            if exc.code == 404:
                raise ReleaseNotFoundError(f"更新资源不存在: {url}") from exc
            raise UpdateNetworkError(f"更新服务器返回 HTTP {exc.code}: {url}") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise UpdateNetworkError(f"无法连接更新服务器: {url}") from exc

    @staticmethod
    def _close(response: Any) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _content_length(response: Any, fallback: int | None) -> int | None:
        headers = getattr(response, "headers", None)
        value = headers.get("Content-Length") if headers is not None else None
        try:
            return int(value) if value is not None else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _raise_if_cancelled(cancel_check: CancelCheck | Any | None) -> None:
        if cancel_check is None:
            return
        cancelled = cancel_check() if callable(cancel_check) else bool(getattr(cancel_check, "is_set", lambda: False)())
        if cancelled:
            raise UpdateCancelledError("更新下载已取消")
