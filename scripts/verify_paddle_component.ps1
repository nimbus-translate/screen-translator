[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string] $ArchivePath,

    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string] $ManifestPath,

    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string] $ManifestSignaturePath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$archive = (Resolve-Path -LiteralPath $ArchivePath).Path
$manifestFile = (Resolve-Path -LiteralPath $ManifestPath).Path
$manifestSignatureFile = (Resolve-Path -LiteralPath $ManifestSignaturePath).Path

Add-Type -AssemblyName System.Security
$manifestBytes = [IO.File]::ReadAllBytes($manifestFile)
$contentInfo = [System.Security.Cryptography.Pkcs.ContentInfo]::new($manifestBytes)
$cms = [System.Security.Cryptography.Pkcs.SignedCms]::new($contentInfo, $true)
$cms.Decode([IO.File]::ReadAllBytes($manifestSignatureFile))
if ($cms.SignerInfos.Count -ne 1) { throw "组件 manifest 必须且只能有一个 CMS 签名者。" }
$cms.CheckSignature($false)
$manifestSignerInfo = $cms.SignerInfos[0]
if ($manifestSignerInfo.DigestAlgorithm.Value -ne "2.16.840.1.101.3.4.2.1") {
    throw "组件 manifest CMS 必须使用 SHA-256。"
}
$manifestSigner = $manifestSignerInfo.Certificate
if ($null -eq $manifestSigner) { throw "组件 manifest 没有签名证书。" }

$ekuExtension = $manifestSigner.Extensions |
    Where-Object { $_.Oid.Value -eq "2.5.29.37" } |
    Select-Object -First 1
if ($null -eq $ekuExtension) { throw "组件 manifest 签名证书没有代码签名用途。" }
$eku = [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]$ekuExtension
$codeSigningUsage = $eku.EnhancedKeyUsages |
    Where-Object { $_.Value -eq "1.3.6.1.5.5.7.3.3" -or $_.Value -eq "2.5.29.37.0" } |
    Select-Object -First 1
if ($null -eq $codeSigningUsage) { throw "组件 manifest 签名证书不允许代码签名。" }

$manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding utf8 | ConvertFrom-Json
$archiveSize = (Get-Item -LiteralPath $archive).Length
$archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()

if ([int]$manifest.schema_version -ne 1) { throw "组件 manifest 协议版本错误。" }
if ([long]$manifest.size -ne $archiveSize) { throw "组件 manifest size 与 ZIP 不一致。" }
if ([string]$manifest.sha256 -ne $archiveHash) { throw "组件 manifest SHA-256 与 ZIP 不一致。" }
if ([string]$manifest.entrypoint -ne "PaddleOCRComponent.exe") { throw "组件入口契约错误。" }
if (-not ([Uri]$manifest.url).Scheme.Equals("https", [StringComparison]::OrdinalIgnoreCase)) {
    throw "组件 manifest URL 必须使用 HTTPS。"
}

$verificationRoot = Join-Path ([IO.Path]::GetTempPath()) ("screen-translator-component-verify-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $verificationRoot | Out-Null
try {
    Expand-Archive -LiteralPath $archive -DestinationPath $verificationRoot
    $catalogFile = Join-Path $verificationRoot "component-files.json"
    $catalogSignatureFile = Join-Path $verificationRoot "component-files.p7s"
    if (-not (Test-Path -LiteralPath $catalogFile -PathType Leaf) -or
        -not (Test-Path -LiteralPath $catalogSignatureFile -PathType Leaf)) {
        throw "组件 ZIP 缺少已签名的文件目录。"
    }

    $catalogBytes = [IO.File]::ReadAllBytes($catalogFile)
    $catalogContent = [System.Security.Cryptography.Pkcs.ContentInfo]::new($catalogBytes)
    $catalogCms = [System.Security.Cryptography.Pkcs.SignedCms]::new($catalogContent, $true)
    $catalogCms.Decode([IO.File]::ReadAllBytes($catalogSignatureFile))
    if ($catalogCms.SignerInfos.Count -ne 1) { throw "组件文件目录必须且只能有一个 CMS 签名者。" }
    $catalogCms.CheckSignature($false)
    $catalogSignerInfo = $catalogCms.SignerInfos[0]
    if ($catalogSignerInfo.DigestAlgorithm.Value -ne "2.16.840.1.101.3.4.2.1") {
        throw "组件文件目录 CMS 必须使用 SHA-256。"
    }
    $catalogSigner = $catalogSignerInfo.Certificate
    if ($null -eq $catalogSigner -or -not [string]::Equals(
        $catalogSigner.Thumbprint,
        $manifestSigner.Thumbprint,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "组件文件目录与外层 manifest 不是同一发布证书。"
    }

    $catalog = Get-Content -LiteralPath $catalogFile -Raw -Encoding utf8 | ConvertFrom-Json
    if ([int]$catalog.schema_version -ne 1) { throw "组件文件目录协议版本错误。" }
    $records = @($catalog.files)
    if ($records.Count -eq 0 -or $records.Count -gt 100000) {
        throw "组件文件目录记录数量异常。"
    }
    $rootPrefix = [IO.Path]::GetFullPath($verificationRoot).TrimEnd("\") + "\"
    $excluded = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    [void]$excluded.Add("component-files.json")
    [void]$excluded.Add("component-files.p7s")
    [void]$excluded.Add(".component-manifest.json")
    $expected = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($record in $records) {
        $relativePath = [string]$record.path
        $parts = @($relativePath.Split("/"))
        if ([string]::IsNullOrWhiteSpace($relativePath) -or
            $relativePath.StartsWith("/") -or
            $relativePath.Contains("\") -or
            $relativePath.Contains(":") -or
            $relativePath.Contains([char]0) -or
            $parts -contains "" -or
            $parts -contains "." -or
            $parts -contains ".." -or
            $excluded.Contains($relativePath)) {
            throw "组件文件目录包含不安全或保留路径。"
        }
        if ($expected.ContainsKey($relativePath)) { throw "组件文件目录包含重复路径。" }
        $size = [long]$record.size
        $digest = [string]$record.sha256
        if ($size -lt 0 -or $digest -notmatch '^[0-9a-fA-F]{64}$') {
            throw "组件文件目录记录无效：$relativePath"
        }
        $candidate = [IO.Path]::GetFullPath((Join-Path $verificationRoot $relativePath.Replace("/", "\")))
        if (-not $candidate.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "组件文件目录路径越界：$relativePath"
        }
        $expected.Add($relativePath, [pscustomobject]@{ Path = $candidate; Size = $size; Sha256 = $digest })
    }

    $actual = [Collections.Generic.Dictionary[string, string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($item in Get-ChildItem -LiteralPath $verificationRoot -Force -Recurse) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "组件 ZIP 包含链接或重解析点。"
        }
        if ($item.PSIsContainer) { continue }
        $fullPath = [IO.Path]::GetFullPath($item.FullName)
        if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "组件 ZIP 文件越出校验目录。"
        }
        $relativePath = $fullPath.Substring($rootPrefix.Length).Replace("\", "/")
        if ($excluded.Contains($relativePath)) { continue }
        if ($actual.ContainsKey($relativePath)) { throw "组件 ZIP 包含大小写冲突路径。" }
        $actual.Add($relativePath, $fullPath)
    }
    if ($actual.Count -ne $expected.Count) { throw "组件文件集合与签名目录不一致。" }
    [long]$catalogedBytes = 0
    foreach ($relativePath in $expected.Keys) {
        if (-not $actual.ContainsKey($relativePath)) { throw "组件缺少签名目录中的文件：$relativePath" }
        $record = $expected[$relativePath]
        $file = Get-Item -LiteralPath $actual[$relativePath]
        if ([long]$file.Length -ne [long]$record.Size) { throw "组件文件大小校验失败：$relativePath" }
        $catalogedBytes += [long]$file.Length
        if ($catalogedBytes -gt 8GB) { throw "组件文件总大小异常。" }
        $actualHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        if (-not [string]::Equals($actualHash, [string]$record.Sha256, [StringComparison]::OrdinalIgnoreCase)) {
            throw "组件文件 SHA-256 校验失败：$relativePath"
        }
    }

    $entrypoint = Join-Path $verificationRoot "PaddleOCRComponent.exe"
    if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) { throw "ZIP 根目录缺少组件入口。" }

    $signature = Get-AuthenticodeSignature -LiteralPath $entrypoint
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "组件入口 Authenticode 无效：$($signature.Status)"
    }
    if (-not [string]::Equals(
        $signature.SignerCertificate.Thumbprint,
        $manifestSigner.Thumbprint,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "组件入口与 manifest 不是同一发布证书。"
    }

    $models = Join-Path $verificationRoot "_internal\models"
    if (-not (Test-Path -LiteralPath $models -PathType Container) -or
        (Get-ChildItem -LiteralPath $models -File -Recurse | Select-Object -First 1).Count -eq 0) {
        throw "组件 ZIP 没有预下载模型。"
    }

    & $entrypoint --warmup
    if ($LASTEXITCODE -ne 0) { throw "解压后的 PaddleOCR worker 预热失败。" }

    # The worker must treat its signed onedir payload as immutable.  Rebuild
    # the exact file set and hashes after warmup so newly written cache files
    # or modified model metadata fail the release before it is published.
    $actualAfterWarmup = [Collections.Generic.Dictionary[string, string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($item in Get-ChildItem -LiteralPath $verificationRoot -Force -Recurse) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "组件预热后出现链接或重解析点。"
        }
        if ($item.PSIsContainer) { continue }
        $fullPath = [IO.Path]::GetFullPath($item.FullName)
        if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "组件预热后文件越出校验目录。"
        }
        $relativePath = $fullPath.Substring($rootPrefix.Length).Replace("\", "/")
        if ($excluded.Contains($relativePath)) { continue }
        if ($actualAfterWarmup.ContainsKey($relativePath)) { throw "组件预热后出现大小写冲突路径。" }
        $actualAfterWarmup.Add($relativePath, $fullPath)
    }
    if ($actualAfterWarmup.Count -ne $expected.Count) {
        throw "组件预热改变了签名文件集合。"
    }
    [long]$bytesAfterWarmup = 0
    foreach ($relativePath in $expected.Keys) {
        if (-not $actualAfterWarmup.ContainsKey($relativePath)) {
            throw "组件预热删除了签名文件：$relativePath"
        }
        $record = $expected[$relativePath]
        $file = Get-Item -LiteralPath $actualAfterWarmup[$relativePath]
        if ([long]$file.Length -ne [long]$record.Size) {
            throw "组件预热改变了文件大小：$relativePath"
        }
        $bytesAfterWarmup += [long]$file.Length
        if ($bytesAfterWarmup -gt 8GB) { throw "组件预热后文件总大小异常。" }
        $actualHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        if (-not [string]::Equals($actualHash, [string]$record.Sha256, [StringComparison]::OrdinalIgnoreCase)) {
            throw "组件预热改变了文件内容：$relativePath"
        }
    }
}
finally {
    $resolvedVerification = [IO.Path]::GetFullPath($verificationRoot)
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolvedVerification.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedVerification).StartsWith("screen-translator-component-verify-")) {
        Remove-Item -LiteralPath $resolvedVerification -Recurse -Force -ErrorAction SilentlyContinue
    }
}
