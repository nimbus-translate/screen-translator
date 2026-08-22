"""Verified lifecycle management for the optional PaddleOCR component.

The light application never imports Paddle.  It only downloads and launches this
separate, signed-release component after checking its manifest and archive.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import zipfile

from services.authenticode import (
    runtime_signature_reference,
    verify_authenticode,
    verify_detached_cms,
)


DEFAULT_MANIFEST_URL = (
    "https://github.com/nimbus-translate/screen-translator/releases/latest/download/"
    "paddle-component-manifest.json"
)
DEFAULT_MANIFEST_SIGNATURE_URL = (
    "https://github.com/nimbus-translate/screen-translator/releases/latest/download/"
    "paddle-component-manifest.p7s"
)

_MAXIMUM_MANIFEST_BYTES = 1024 * 1024
_MAXIMUM_MANIFEST_SIGNATURE_BYTES = 1024 * 1024
_MAXIMUM_COMPONENT_CATALOG_BYTES = 16 * 1024 * 1024
_MAXIMUM_COMPONENT_FILES = 100_000
_MAXIMUM_COMPONENT_BYTES = 8 * 1024**3

COMPONENT_CATALOG_FILENAME = "component-files.json"
COMPONENT_CATALOG_SIGNATURE_FILENAME = "component-files.p7s"
_LOCAL_COMPONENT_MANIFEST_FILENAME = ".component-manifest.json"

ProgressCallback = Callable[[int, int | None], None]
CancelCheck = Callable[[], bool]
ManifestSignatureVerifier = Callable[[bytes, bytes], None]


def _derive_manifest_signature_url(manifest_url: str) -> str:
    parts = urlsplit(manifest_url)
    path = parts.path
    signature_path = path[:-5] + ".p7s" if path.casefold().endswith(".json") else path + ".p7s"
    return urlunsplit((parts.scheme, parts.netloc, signature_path, parts.query, parts.fragment))


class PaddleComponentError(RuntimeError):
    """Base error for the optional Paddle component."""


class ComponentDownloadError(PaddleComponentError):
    pass


class ComponentCancelledError(PaddleComponentError):
    pass


class ComponentVerificationError(PaddleComponentError):
    pass


class ComponentInstallError(PaddleComponentError):
    pass


@dataclass(frozen=True)
class PaddleComponentManifest:
    """Release asset metadata published as ``paddle-component-manifest.json``."""

    schema_version: int
    version: str
    url: str
    sha256: str
    size: int | None
    entrypoint: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PaddleComponentManifest":
        if data.get("schema_version") != 1:
            raise ComponentVerificationError("不支持的组件 manifest 协议版本")
        required = ("version", "url", "sha256", "entrypoint")
        missing = [key for key in required if not isinstance(data.get(key), str) or not data[key].strip()]
        if missing:
            raise ComponentVerificationError("组件 manifest 缺少字段: " + ", ".join(missing))
        url = str(data["url"])
        if urlparse(url).scheme.lower() != "https":
            raise ComponentVerificationError("组件下载地址必须使用 HTTPS")
        digest = str(data["sha256"]).strip().lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ComponentVerificationError("组件 manifest 的 SHA-256 无效")
        entrypoint = str(data["entrypoint"])
        entry_path = PurePosixPath(entrypoint.replace("\\", "/"))
        if (
            entry_path.is_absolute()
            or ".." in entry_path.parts
            or any(":" in part for part in entry_path.parts)
            or entrypoint.strip() in ("", ".")
            or entry_path.suffix.casefold() != ".exe"
        ):
            raise ComponentVerificationError("组件 entrypoint 路径不安全")
        raw_size = data.get("size")
        if raw_size is not None and (not isinstance(raw_size, int) or raw_size < 0):
            raise ComponentVerificationError("组件 manifest 的 size 无效")
        return cls(1, str(data["version"]).strip(), url, digest, raw_size, entry_path.as_posix())


class PaddleComponentManager:
    """Download, verify and atomically activate an onedir Paddle worker.

    Main-program integration is intentionally small::

        manager = PaddleComponentManager(app_data / "components" )
        manager.ensure_installed(progress_callback=show_download_progress)
        result = manager.run_ocr(image_path, lang="japan")

    ``run_ocr`` invokes the component executable; the light process does not
    dynamically import Paddle/PaddleOCR/PaddleX.
    """

    def __init__(
        self,
        install_root: str | os.PathLike[str],
        *,
        manifest_url: str = DEFAULT_MANIFEST_URL,
        timeout_seconds: float = 30.0,
        opener: Callable[..., Any] | None = None,
        signature_verifier: Callable[[Path], None] | None = None,
        manifest_signature_url: str | None = None,
        manifest_signature_verifier: ManifestSignatureVerifier | None = None,
        catalog_signature_verifier: ManifestSignatureVerifier | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if urlparse(manifest_url).scheme.lower() != "https":
            raise ValueError("manifest_url 必须使用 HTTPS")
        resolved_signature_url = manifest_signature_url or (
            DEFAULT_MANIFEST_SIGNATURE_URL
            if manifest_url == DEFAULT_MANIFEST_URL
            else _derive_manifest_signature_url(manifest_url)
        )
        if urlparse(resolved_signature_url).scheme.lower() != "https":
            raise ValueError("manifest_signature_url 必须使用 HTTPS")
        self.install_root = Path(install_root)
        self.manifest_url = manifest_url
        self.manifest_signature_url = resolved_signature_url
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urlopen
        self._signature_verifier = signature_verifier or (
            lambda path: verify_authenticode(
                path,
                reference_path=runtime_signature_reference(),
            )
        )
        default_detached_verifier = (
            lambda content, signature: verify_detached_cms(
                content,
                signature,
                reference_path=runtime_signature_reference(),
            )
        )
        self._manifest_signature_verifier = manifest_signature_verifier or default_detached_verifier
        # Tests and embedders can supply one detached-signature boundary for
        # both release metadata and the component's signed file catalog.
        self._catalog_signature_verifier = (
            catalog_signature_verifier
            or manifest_signature_verifier
            or default_detached_verifier
        )
        self._verified_entrypoint_identity: tuple[Path, int, int] | None = None
        self._verified_tree_root: Path | None = None

    @property
    def active_root(self) -> Path:
        return self.install_root / "active"

    @property
    def active_manifest_path(self) -> Path:
        return self.active_root / ".component-manifest.json"

    def fetch_manifest(self, *, cancel_check: CancelCheck | Any | None = None) -> PaddleComponentManifest:
        raw = self._read_all(
            self.manifest_url,
            cancel_check=cancel_check,
            maximum_bytes=_MAXIMUM_MANIFEST_BYTES,
        )
        signature = self._read_all(
            self.manifest_signature_url,
            cancel_check=cancel_check,
            maximum_bytes=_MAXIMUM_MANIFEST_SIGNATURE_BYTES,
        )
        try:
            # The signature authenticates the archive URL and SHA-256.  JSON is
            # deliberately not parsed until this trust boundary succeeds.
            self._manifest_signature_verifier(raw, signature)
        except Exception as exc:
            raise ComponentVerificationError(f"组件 manifest 签名验证失败：{exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComponentVerificationError("组件 manifest 不是有效 JSON") from exc
        if not isinstance(data, Mapping):
            raise ComponentVerificationError("组件 manifest 必须是 JSON 对象")
        return PaddleComponentManifest.from_mapping(data)

    def installed_manifest(self) -> PaddleComponentManifest | None:
        try:
            data = json.loads(self.active_manifest_path.read_text(encoding="utf-8"))
            manifest = PaddleComponentManifest.from_mapping(data)
            entrypoint = self.active_root / Path(*PurePosixPath(manifest.entrypoint).parts)
            catalog = self.active_root / COMPONENT_CATALOG_FILENAME
            catalog_signature = self.active_root / COMPONENT_CATALOG_SIGNATURE_FILENAME
            return manifest if (
                entrypoint.is_file()
                and catalog.is_file()
                and catalog.stat().st_size > 0
                and catalog_signature.is_file()
                and catalog_signature.stat().st_size > 0
            ) else None
        except (OSError, json.JSONDecodeError, ComponentVerificationError):
            return None

    def is_installed(self, manifest: PaddleComponentManifest | None = None) -> bool:
        installed = self.installed_manifest()
        return installed is not None and (manifest is None or installed.version == manifest.version and installed.sha256 == manifest.sha256)

    def ensure_installed(
        self,
        manifest: PaddleComponentManifest | Mapping[str, Any] | None = None,
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_check: CancelCheck | Any | None = None,
        chunk_size: int = 256 * 1024,
    ) -> Path:
        """Install the requested release without ever exposing a half-install."""
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")
        resolved = self.fetch_manifest(cancel_check=cancel_check) if manifest is None else (
            manifest if isinstance(manifest, PaddleComponentManifest) else PaddleComponentManifest.from_mapping(manifest)
        )
        if self.is_installed(resolved):
            return self.component_entrypoint()
        self._raise_if_cancelled(cancel_check)
        self.install_root.mkdir(parents=True, exist_ok=True)
        archive = self._download_archive(resolved, progress_callback, cancel_check, chunk_size)
        try:
            self._activate_archive(archive, resolved, cancel_check)
        finally:
            archive.unlink(missing_ok=True)
        return self.component_entrypoint()

    def component_entrypoint(self) -> Path:
        manifest = self.installed_manifest()
        if manifest is None:
            raise ComponentInstallError("PaddleOCR 组件未安装或安装不完整")
        entrypoint = self.active_root / Path(*PurePosixPath(manifest.entrypoint).parts)
        if not entrypoint.is_file():
            raise ComponentInstallError("PaddleOCR 组件入口不存在")
        try:
            resolved_root = self.active_root.resolve(strict=True)
            if resolved_root != self._verified_tree_root:
                self._verify_component_tree(self.active_root)
                self._verified_tree_root = resolved_root
            stat_result = entrypoint.stat()
            identity = (entrypoint.resolve(), stat_result.st_size, stat_result.st_mtime_ns)
            if identity != self._verified_entrypoint_identity:
                self._signature_verifier(entrypoint)
                self._verified_entrypoint_identity = identity
        except Exception as exc:
            raise ComponentVerificationError(f"组件数字签名验证失败：{exc}") from exc
        return entrypoint

    def run_ocr(
        self,
        input_png: str | os.PathLike[str],
        *,
        lang: str,
        timeout_seconds: float = 90.0,
    ) -> list[dict[str, Any]]:
        """Run one OCR job through the isolated worker and return JSON lines."""
        input_path = Path(input_png)
        if not input_path.is_file():
            raise FileNotFoundError(input_path)
        with tempfile.TemporaryDirectory(prefix="screen-translator-ocr-") as temp_dir:
            output_path = Path(temp_dir) / "result.json"
            command = [str(self.component_entrypoint()), "--input", str(input_path), "--lang", lang, "--output", str(output_path)]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                    creationflags=self._worker_creation_flags(),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PaddleComponentError(f"PaddleOCR worker 无法运行: {exc}") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                raise PaddleComponentError(f"PaddleOCR worker 失败 ({completed.returncode}): {detail}")
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PaddleComponentError("PaddleOCR worker 未生成有效 JSON") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("lines"), list):
            raise PaddleComponentError("PaddleOCR worker 返回格式错误")
        return payload["lines"]

    def warmup(self, *, timeout_seconds: float = 180.0) -> None:
        """Load the component models in a short-lived worker before first OCR."""
        try:
            completed = subprocess.run(
                [str(self.component_entrypoint()), "--warmup"],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                creationflags=self._worker_creation_flags(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PaddleComponentError(f"PaddleOCR worker 预热失败: {exc}") from exc
        if completed.returncode != 0:
            raise PaddleComponentError((completed.stderr or completed.stdout or "PaddleOCR worker 预热失败").strip())

    @staticmethod
    def _worker_creation_flags() -> int:
        """Prevent the console worker from flashing a terminal on Windows."""
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0

    def _download_archive(self, manifest: PaddleComponentManifest, progress: ProgressCallback | None, cancel: CancelCheck | Any | None, chunk_size: int) -> Path:
        fd, name = tempfile.mkstemp(prefix=".paddle-component-", suffix=".zip", dir=self.install_root)
        archive = Path(name)
        downloaded, hasher = 0, hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as stream:
                response = self._open(manifest.url)
                try:
                    total = self._content_length(response, manifest.size)
                    if progress:
                        progress(0, total)
                    while True:
                        self._raise_if_cancelled(cancel)
                        block = response.read(chunk_size)
                        if not block:
                            break
                        stream.write(block)
                        hasher.update(block)
                        downloaded += len(block)
                        if progress:
                            progress(downloaded, total)
                    stream.flush()
                    os.fsync(stream.fileno())
                finally:
                    self._close(response)
            if manifest.size is not None and downloaded != manifest.size:
                raise ComponentVerificationError("组件下载大小与 manifest 不一致")
            if hasher.hexdigest().casefold() != manifest.sha256.casefold():
                raise ComponentVerificationError("PaddleOCR 组件 SHA-256 校验失败")
            self._raise_if_cancelled(cancel)
            return archive
        except Exception:
            archive.unlink(missing_ok=True)
            raise

    def _activate_archive(self, archive: Path, manifest: PaddleComponentManifest, cancel: CancelCheck | Any | None) -> None:
        staging = Path(tempfile.mkdtemp(prefix=".paddle-component-", dir=self.install_root))
        old = self.install_root / ".active-old"
        try:
            self._safe_extract(archive, staging, cancel)
            self._raise_if_cancelled(cancel)
            entrypoint = staging / Path(*PurePosixPath(manifest.entrypoint).parts)
            if not entrypoint.is_file():
                raise ComponentInstallError("组件压缩包中没有 manifest 指定的入口")
            self._verify_component_tree(staging, cancel)
            try:
                self._signature_verifier(entrypoint)
            except Exception as exc:
                raise ComponentVerificationError(f"组件数字签名验证失败：{exc}") from exc
            manifest_file = staging / ".component-manifest.json"
            manifest_file.write_text(json.dumps(manifest.__dict__, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            # Same-volume replace keeps activation atomic. Retain the prior working
            # component until the new directory has been fully verified.
            if old.exists():
                shutil.rmtree(old)
            if self.active_root.exists():
                os.replace(self.active_root, old)
            try:
                os.replace(staging, self.active_root)
            except Exception:
                if old.exists() and not self.active_root.exists():
                    os.replace(old, self.active_root)
                raise
            self._verified_tree_root = self.active_root.resolve(strict=True)
            self._verified_entrypoint_identity = None
            # Cleanup is best effort.  A locked stale backup does not make the
            # newly activated, fully verified component incomplete.
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ComponentInstallError(f"PaddleOCR 组件安装失败: {exc}") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _verify_component_tree(
        self,
        root: Path,
        cancel_check: CancelCheck | Any | None = None,
    ) -> None:
        """Authenticate and hash every installed component payload file once."""
        catalog_path = root / COMPONENT_CATALOG_FILENAME
        signature_path = root / COMPONENT_CATALOG_SIGNATURE_FILENAME
        try:
            if self._is_link_or_reparse(root):
                raise ComponentVerificationError("组件根目录不能是链接或重解析点")
            catalog_bytes = self._read_limited_file(
                catalog_path,
                maximum_bytes=_MAXIMUM_COMPONENT_CATALOG_BYTES,
                label="组件文件目录",
            )
            signature_bytes = self._read_limited_file(
                signature_path,
                maximum_bytes=_MAXIMUM_MANIFEST_SIGNATURE_BYTES,
                label="组件文件目录签名",
            )
            try:
                self._catalog_signature_verifier(catalog_bytes, signature_bytes)
            except Exception as exc:
                raise ComponentVerificationError(f"组件文件目录签名验证失败：{exc}") from exc
            expected = self._parse_component_catalog(catalog_bytes)
            actual = self._component_payload_files(root)
            expected_paths = set(expected)
            actual_paths = set(actual)
            if expected_paths != actual_paths:
                missing = sorted(expected_paths - actual_paths)
                extra = sorted(actual_paths - expected_paths)
                detail = []
                if missing:
                    detail.append("缺少 " + ", ".join(missing[:3]))
                if extra:
                    detail.append("多出 " + ", ".join(extra[:3]))
                raise ComponentVerificationError(
                    "组件文件集合与签名目录不一致" + ("（" + "；".join(detail) + "）" if detail else "")
                )
            total_size = 0
            for relative_path in sorted(expected):
                self._raise_if_cancelled(cancel_check)
                path = actual[relative_path]
                record_size, record_hash = expected[relative_path]
                before = path.stat()
                if before.st_size != record_size:
                    raise ComponentVerificationError(f"组件文件大小校验失败：{relative_path}")
                total_size += before.st_size
                if total_size > _MAXIMUM_COMPONENT_BYTES:
                    raise ComponentVerificationError("组件文件总大小异常")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while True:
                        block = stream.read(1024 * 1024)
                        if not block:
                            break
                        digest.update(block)
                        self._raise_if_cancelled(cancel_check)
                after = path.stat()
                if (
                    before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                    or digest.hexdigest().casefold() != record_hash
                ):
                    raise ComponentVerificationError(f"组件文件 SHA-256 校验失败：{relative_path}")
        except ComponentVerificationError:
            raise
        except OSError as exc:
            raise ComponentVerificationError(f"无法验证组件文件完整性：{exc}") from exc

    @staticmethod
    def _read_limited_file(path: Path, *, maximum_bytes: int, label: str) -> bytes:
        try:
            size = path.stat().st_size
            if size <= 0 or size > maximum_bytes:
                raise ComponentVerificationError(f"{label}大小无效")
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ComponentVerificationError(f"缺少{label}") from exc

    @staticmethod
    def _parse_component_catalog(raw: bytes) -> dict[str, tuple[int, str]]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComponentVerificationError("组件文件目录不是有效 JSON") from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            raise ComponentVerificationError("不支持的组件文件目录协议版本")
        records = payload.get("files")
        if not isinstance(records, list) or not records or len(records) > _MAXIMUM_COMPONENT_FILES:
            raise ComponentVerificationError("组件文件目录的 files 无效")
        parsed: dict[str, tuple[int, str]] = {}
        casefolded: set[str] = set()
        reserved = {
            COMPONENT_CATALOG_FILENAME.casefold(),
            COMPONENT_CATALOG_SIGNATURE_FILENAME.casefold(),
            _LOCAL_COMPONENT_MANIFEST_FILENAME.casefold(),
        }
        for record in records:
            if not isinstance(record, Mapping):
                raise ComponentVerificationError("组件文件目录记录无效")
            raw_path = record.get("path")
            if not isinstance(raw_path, str):
                raise ComponentVerificationError("组件文件目录路径无效")
            relative = PaddleComponentManager._safe_relative_file_path(raw_path)
            folded = relative.casefold()
            if folded in reserved or folded in casefolded:
                raise ComponentVerificationError("组件文件目录包含保留或重复路径")
            casefolded.add(folded)
            size = record.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ComponentVerificationError(f"组件文件大小无效：{relative}")
            digest = record.get("sha256")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdefABCDEF" for character in digest)
            ):
                raise ComponentVerificationError(f"组件文件 SHA-256 无效：{relative}")
            parsed[relative] = (size, digest.casefold())
        return parsed

    @staticmethod
    def _safe_relative_file_path(value: str) -> str:
        if not value or "\\" in value or "\x00" in value:
            raise ComponentVerificationError("组件文件目录路径不安全")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.as_posix() != value
            or any(part in ("", ".", "..") or ":" in part for part in path.parts)
        ):
            raise ComponentVerificationError("组件文件目录路径不安全")
        return path.as_posix()

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        metadata = path.lstat()
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)

    @classmethod
    def _component_payload_files(cls, root: Path) -> dict[str, Path]:
        excluded = {
            COMPONENT_CATALOG_FILENAME.casefold(),
            COMPONENT_CATALOG_SIGNATURE_FILENAME.casefold(),
            _LOCAL_COMPONENT_MANIFEST_FILENAME.casefold(),
        }
        payload: dict[str, Path] = {}
        casefolded: set[str] = set()
        for current_root, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current = Path(current_root)
            for directory_name in directory_names:
                directory = current / directory_name
                if cls._is_link_or_reparse(directory):
                    raise ComponentVerificationError("组件目录不能包含链接或重解析点")
            for file_name in file_names:
                file_path = current / file_name
                if cls._is_link_or_reparse(file_path) or not file_path.is_file():
                    raise ComponentVerificationError("组件目录只能包含普通文件")
                relative = file_path.relative_to(root).as_posix()
                if relative.casefold() in excluded:
                    continue
                safe_relative = cls._safe_relative_file_path(relative)
                folded = safe_relative.casefold()
                if folded in casefolded:
                    raise ComponentVerificationError("组件目录包含大小写冲突路径")
                casefolded.add(folded)
                payload[safe_relative] = file_path
                if len(payload) > _MAXIMUM_COMPONENT_FILES:
                    raise ComponentVerificationError("组件文件数量异常")
        return payload

    @staticmethod
    def _safe_extract(
        archive: Path,
        destination: Path,
        cancel_check: CancelCheck | Any | None = None,
    ) -> None:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if len(members) > 100_000 or sum(member.file_size for member in members) > 8 * 1024**3:
                raise ComponentInstallError("组件压缩包解压规模异常")
            seen_paths: set[str] = set()
            for member in members:
                name = PurePosixPath(member.filename)
                if (
                    name.is_absolute()
                    or ".." in name.parts
                    or any(":" in part for part in name.parts)
                    or not member.filename
                    or "\x00" in member.filename
                    or "\\" in member.filename
                ):
                    raise ComponentInstallError("组件压缩包包含不安全路径")
                normalized = name.as_posix().rstrip("/").casefold()
                if not normalized or normalized in seen_paths:
                    raise ComponentInstallError("组件压缩包包含重复路径")
                seen_paths.add(normalized)
                # Unix symlinks can otherwise escape the validated destination.
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ComponentInstallError("组件压缩包不允许符号链接")
                target = destination.joinpath(*name.parts)
                try:
                    target.resolve().relative_to(destination.resolve())
                except ValueError as exc:
                    raise ComponentInstallError("组件压缩包路径越界") from exc
            for member in members:
                PaddleComponentManager._raise_if_cancelled(cancel_check)
                package.extract(member, destination)

    def _read_all(
        self,
        url: str,
        *,
        cancel_check: CancelCheck | Any | None,
        maximum_bytes: int | None = None,
    ) -> bytes:
        response = self._open(url)
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                self._raise_if_cancelled(cancel_check)
                data = response.read(64 * 1024)
                if not data:
                    return b"".join(chunks)
                total += len(data)
                if maximum_bytes is not None and total > maximum_bytes:
                    raise ComponentVerificationError("组件 manifest 或签名响应过大")
                chunks.append(data)
        finally:
            self._close(response)

    def _open(self, url: str) -> Any:
        if urlparse(url).scheme.lower() != "https":
            raise ComponentDownloadError("组件下载地址必须使用 HTTPS")
        try:
            return self._opener(Request(url, headers={"User-Agent": "ScreenTranslator-PaddleComponent/1.0"}), timeout=self.timeout_seconds)
        except HTTPError as exc:
            raise ComponentDownloadError(f"组件服务器返回 HTTP {exc.code}") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise ComponentDownloadError(f"无法下载 PaddleOCR 组件: {exc}") from exc

    @staticmethod
    def _content_length(response: Any, fallback: int | None) -> int | None:
        try:
            value = getattr(response, "headers", {}).get("Content-Length")
            return int(value) if value is not None else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _close(response: Any) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _raise_if_cancelled(cancel_check: CancelCheck | Any | None) -> None:
        if cancel_check is None:
            return
        cancelled = cancel_check() if callable(cancel_check) else bool(getattr(cancel_check, "is_set", lambda: False)())
        if cancelled:
            raise ComponentCancelledError("PaddleOCR 组件下载已取消")
