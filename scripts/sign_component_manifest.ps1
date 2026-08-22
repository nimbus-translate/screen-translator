[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string] $Path,

    [Parameter(Mandatory = $true)]
    [string] $OutputPath,

    [string] $PfxBase64 = $env:WINDOWS_CERTIFICATE_PFX_BASE64,
    [string] $PfxPassword = $env:WINDOWS_CERTIFICATE_PASSWORD
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Test-CodeSigningCertificate {
    param(
        [Parameter(Mandatory = $true)]
        [System.Security.Cryptography.X509Certificates.X509Certificate2] $Certificate
    )

    $eku = $Certificate.Extensions |
        Where-Object { $_.Oid.Value -eq "2.5.29.37" } |
        Select-Object -First 1
    if ($null -eq $eku) { return $false }
    $enhanced = [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]$eku
    return $null -ne ($enhanced.EnhancedKeyUsages |
        Where-Object { $_.Value -eq "1.3.6.1.5.5.7.3.3" -or $_.Value -eq "2.5.29.37.0" } |
        Select-Object -First 1)
}

if ([string]::IsNullOrWhiteSpace($PfxBase64) -or [string]::IsNullOrWhiteSpace($PfxPassword)) {
    throw "缺少组件 manifest 签名凭据。"
}

$contentPath = (Resolve-Path -LiteralPath $Path).Path
$signaturePath = [System.IO.Path]::GetFullPath($OutputPath)
if ($contentPath.Equals($signaturePath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputPath 不能覆盖待签名 manifest。"
}
if (Test-Path -LiteralPath $signaturePath) {
    throw "签名输出已存在，拒绝覆盖：$signaturePath"
}
$signatureDirectory = Split-Path -Parent $signaturePath
if (-not (Test-Path -LiteralPath $signatureDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $signatureDirectory | Out-Null
}

$pfxBytes = $null
$certificate = $null
$temporarySignature = Join-Path $signatureDirectory ("." + [IO.Path]::GetFileName($signaturePath) + "." + [guid]::NewGuid().ToString("N") + ".tmp")
try {
    try {
        $pfxBytes = [Convert]::FromBase64String($PfxBase64)
    }
    catch {
        throw "WINDOWS_CERTIFICATE_PFX_BASE64 不是有效的 Base64 PFX。"
    }

    $flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
    $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $pfxBytes,
        $PfxPassword,
        $flags
    )
    if (-not $certificate.HasPrivateKey) { throw "PFX 不包含私钥。" }
    if (-not (Test-CodeSigningCertificate -Certificate $certificate)) {
        throw "PFX 证书不允许代码签名。"
    }

    Add-Type -AssemblyName System.Security
    $contentBytes = [IO.File]::ReadAllBytes($contentPath)
    $contentInfo = [System.Security.Cryptography.Pkcs.ContentInfo]::new($contentBytes)
    $cms = [System.Security.Cryptography.Pkcs.SignedCms]::new($contentInfo, $true)
    $signer = [System.Security.Cryptography.Pkcs.CmsSigner]::new($certificate)
    $signer.DigestAlgorithm = [System.Security.Cryptography.Oid]::new("2.16.840.1.101.3.4.2.1")
    $signer.IncludeOption = [System.Security.Cryptography.X509Certificates.X509IncludeOption]::ExcludeRoot
    $cms.ComputeSignature($signer)
    $encoded = $cms.Encode()

    # Verify the detached payload and certificate chain before publishing it.
    $verification = [System.Security.Cryptography.Pkcs.SignedCms]::new($contentInfo, $true)
    $verification.Decode($encoded)
    if ($verification.SignerInfos.Count -ne 1) { throw "CMS 签名者数量异常。" }
    $verification.CheckSignature($false)

    [IO.File]::WriteAllBytes($temporarySignature, $encoded)
    Move-Item -LiteralPath $temporarySignature -Destination $signaturePath
}
finally {
    if (Test-Path -LiteralPath $temporarySignature) {
        Remove-Item -LiteralPath $temporarySignature -Force
    }
    if ($null -ne $certificate) { $certificate.Dispose() }
    if ($null -ne $pfxBytes) { [Array]::Clear($pfxBytes, 0, $pfxBytes.Length) }
}
