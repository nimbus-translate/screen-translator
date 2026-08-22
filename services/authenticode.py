"""Windows Authenticode verification for downloaded executable payloads."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


class AuthenticodeVerificationError(RuntimeError):
    """A downloaded executable is unsigned, untrusted, or signed by another publisher."""


class DetachedSignatureVerificationError(RuntimeError):
    """A detached CMS signature is invalid, untrusted, or from another publisher."""


_VERIFY_COMMAND = r"""
$ErrorActionPreference = 'Stop'
$target = Get-AuthenticodeSignature -LiteralPath $env:SCREEN_TRANSLATOR_VERIFY_TARGET
if ($target.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    Write-Error ("Target Authenticode status: " + $target.Status)
    exit 10
}
if (-not [string]::IsNullOrWhiteSpace($env:SCREEN_TRANSLATOR_VERIFY_REFERENCE)) {
    $reference = Get-AuthenticodeSignature -LiteralPath $env:SCREEN_TRANSLATOR_VERIFY_REFERENCE
    if ($reference.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        Write-Error ("Reference Authenticode status: " + $reference.Status)
        exit 11
    }
    if ($target.SignerCertificate.Subject -ne $reference.SignerCertificate.Subject) {
        Write-Error "Authenticode publisher does not match the running application"
        exit 12
    }
}
""".strip()


_VERIFY_DETACHED_CMS_COMMAND = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security

$contentBytes = [System.IO.File]::ReadAllBytes($env:SCREEN_TRANSLATOR_VERIFY_CONTENT)
$signatureBytes = [System.IO.File]::ReadAllBytes($env:SCREEN_TRANSLATOR_VERIFY_SIGNATURE)
$contentInfo = [System.Security.Cryptography.Pkcs.ContentInfo]::new($contentBytes)
$cms = [System.Security.Cryptography.Pkcs.SignedCms]::new($contentInfo, $true)
$cms.Decode($signatureBytes)
if ($cms.SignerInfos.Count -ne 1) {
    Write-Error "Detached CMS must contain exactly one signer"
    exit 20
}

# false means verify both the cryptographic signature and the signer's
# certificate chain against the Windows trust store.
$cms.CheckSignature($false)
$signerInfo = $cms.SignerInfos[0]
if ($signerInfo.DigestAlgorithm.Value -ne '2.16.840.1.101.3.4.2.1') {
    Write-Error "Detached CMS must use SHA-256"
    exit 21
}
$signer = $signerInfo.Certificate
if ($null -eq $signer) {
    Write-Error "Detached CMS does not contain a signer certificate"
    exit 22
}

$codeSigningOid = '1.3.6.1.5.5.7.3.3'
$anyUsageOid = '2.5.29.37.0'
$ekuExtension = $signer.Extensions |
    Where-Object { $_.Oid.Value -eq '2.5.29.37' } |
    Select-Object -First 1
if ($null -eq $ekuExtension) {
    Write-Error "Detached CMS signer is not restricted to code signing"
    exit 23
}
$eku = [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]$ekuExtension
$allowed = @($eku.EnhancedKeyUsages | Where-Object {
    $_.Value -eq $codeSigningOid -or $_.Value -eq $anyUsageOid
})
if ($allowed.Count -eq 0) {
    Write-Error "Detached CMS signer is not valid for code signing"
    exit 24
}

if (-not [string]::IsNullOrWhiteSpace($env:SCREEN_TRANSLATOR_VERIFY_REFERENCE)) {
    $reference = Get-AuthenticodeSignature -LiteralPath $env:SCREEN_TRANSLATOR_VERIFY_REFERENCE
    if ($reference.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        Write-Error ("Reference Authenticode status: " + $reference.Status)
        exit 25
    }
    # Pin the exact release certificate.  Subject-only comparison can be
    # spoofed by another otherwise trusted certificate with the same DN.
    if (-not [string]::Equals(
        $signer.Thumbprint,
        $reference.SignerCertificate.Thumbprint,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        Write-Error "Detached CMS publisher does not match the running application"
        exit 26
    }
}
""".strip()


def _windows_powershell() -> Path:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def runtime_signature_reference() -> Path | None:
    """Use the signed outer PyInstaller executable as the publisher reference."""
    if os.name == "nt" and bool(getattr(sys, "frozen", False)):
        candidate = Path(sys.executable).resolve()
        return candidate if candidate.is_file() else None
    return None


def verify_authenticode(
    path: str | os.PathLike[str],
    *,
    reference_path: str | os.PathLike[str] | None = None,
    timeout_seconds: float = 30.0,
) -> None:
    """Require a valid Windows signature and optionally the same signer subject."""
    target = Path(path).resolve()
    if os.name != "nt":
        raise AuthenticodeVerificationError("Authenticode 校验仅支持 Windows")
    if not target.is_file():
        raise AuthenticodeVerificationError("待校验文件不存在")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")

    reference = Path(reference_path).resolve() if reference_path is not None else None
    if reference is not None and not reference.is_file():
        raise AuthenticodeVerificationError("签名参考程序不存在")

    powershell = _windows_powershell()
    if not powershell.is_file():
        raise AuthenticodeVerificationError("无法找到 Windows PowerShell 以验证数字签名")

    environment = os.environ.copy()
    environment["SCREEN_TRANSLATOR_VERIFY_TARGET"] = str(target)
    environment["SCREEN_TRANSLATOR_VERIFY_REFERENCE"] = str(reference) if reference else ""
    try:
        completed = subprocess.run(
            [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _VERIFY_COMMAND,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuthenticodeVerificationError(f"无法验证数字签名：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "数字签名无效").strip()
        raise AuthenticodeVerificationError(detail)


def verify_detached_cms(
    content: bytes,
    signature: bytes,
    *,
    reference_path: str | os.PathLike[str] | None = None,
    timeout_seconds: float = 30.0,
) -> None:
    """Verify detached CMS bytes against Windows trust and an optional publisher.

    The signed content and signature are passed to PowerShell through private
    temporary files, never interpolated into a command line.  In frozen builds
    callers should pass :func:`runtime_signature_reference` so a valid but
    unrelated code-signing certificate cannot authorize a component manifest.
    """
    if not isinstance(content, bytes) or not content:
        raise ValueError("content 必须是非空 bytes")
    if not isinstance(signature, bytes) or not signature:
        raise ValueError("signature 必须是非空 bytes")
    if os.name != "nt":
        raise DetachedSignatureVerificationError("CMS 签名校验仅支持 Windows")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")

    reference = Path(reference_path).resolve() if reference_path is not None else None
    if reference is not None and not reference.is_file():
        raise DetachedSignatureVerificationError("签名参考程序不存在")

    powershell = _windows_powershell()
    if not powershell.is_file():
        raise DetachedSignatureVerificationError("无法找到 Windows PowerShell 以验证组件 manifest")

    try:
        with tempfile.TemporaryDirectory(prefix="screen-translator-cms-") as temp_dir:
            content_path = Path(temp_dir) / "manifest.bin"
            signature_path = Path(temp_dir) / "manifest.p7s"
            content_path.write_bytes(content)
            signature_path.write_bytes(signature)
            environment = os.environ.copy()
            environment["SCREEN_TRANSLATOR_VERIFY_CONTENT"] = str(content_path)
            environment["SCREEN_TRANSLATOR_VERIFY_SIGNATURE"] = str(signature_path)
            environment["SCREEN_TRANSLATOR_VERIFY_REFERENCE"] = str(reference) if reference else ""
            completed = subprocess.run(
                [
                    str(powershell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    _VERIFY_DETACHED_CMS_COMMAND,
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                env=environment,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DetachedSignatureVerificationError(f"无法验证组件 manifest 签名：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "组件 manifest 签名无效").strip()
        raise DetachedSignatureVerificationError(detail)
