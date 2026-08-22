[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $AssetUrl,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
    [string] $Version,
    [string] $OutputDirectory = "release",
    [string] $ModelDirectory = "",
    [string] $PythonExe = "python",
    [long] $MaximumArchiveBytes = 1900000000,
    [switch] $SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
try {
    $assetUri = [Uri]$AssetUrl
    if ($assetUri.Scheme -ne "https") { throw "AssetUrl 必须使用 HTTPS" }
} catch { throw "AssetUrl 不是有效 HTTPS 地址: $AssetUrl" }
$previousModelDirectory = $env:PADDLE_COMPONENT_MODEL_DIR
if ($ModelDirectory) { $env:PADDLE_COMPONENT_MODEL_DIR = (Resolve-Path -LiteralPath $ModelDirectory) }
Push-Location $root
try {
    if (-not $SkipBuild) {
        & $PythonExe -m PyInstaller --noconfirm --clean paddle_component.spec
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败 ($LASTEXITCODE)" }
    }
    $entrypoint = Join-Path $root "dist\PaddleOCRComponent\PaddleOCRComponent.exe"
    if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
        throw "找不到 PaddleOCR 组件入口：$entrypoint"
    }
    $componentRoot = Join-Path $root "dist\PaddleOCRComponent"
    $catalog = Join-Path $componentRoot "component-files.json"
    $catalogSignature = Join-Path $componentRoot "component-files.p7s"
    if (-not (Test-Path -LiteralPath $catalog -PathType Leaf)) {
        throw "缺少 component-files.json；请在签名入口 EXE 后生成组件文件目录。"
    }
    if (-not (Test-Path -LiteralPath $catalogSignature -PathType Leaf)) {
        throw "缺少 component-files.p7s；拒绝发布未签名的组件文件目录。"
    }
    if ((Get-Item -LiteralPath $catalog).Length -le 0 -or
        (Get-Item -LiteralPath $catalogSignature).Length -le 0) {
        throw "组件文件目录或其签名为空。"
    }
    $out = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
        [System.IO.Path]::GetFullPath($OutputDirectory)
    } else {
        Join-Path $root $OutputDirectory
    }
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $zip = Join-Path $out "PaddleOCRComponent-$Version-windows.zip"
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path (Join-Path $componentRoot "*") -DestinationPath $zip -CompressionLevel Optimal
    $size = (Get-Item -LiteralPath $zip).Length
    if ($size -ge $MaximumArchiveBytes) {
        throw "PaddleOCR 组件为 $size bytes，超过 GitHub Release 安全上限。"
    }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
    $manifest = [ordered]@{ schema_version = 1; version = $Version; url = $AssetUrl; sha256 = $hash; size = $size; entrypoint = "PaddleOCRComponent.exe" } | ConvertTo-Json
    # Windows PowerShell's ``-Encoding utf8`` emits a BOM; Python's strict
    # UTF-8 JSON reader deliberately rejects it, so publish a BOM-free manifest.
    [System.IO.File]::WriteAllText((Join-Path $out "paddle-component-manifest.json"), $manifest, [System.Text.UTF8Encoding]::new($false))
} finally {
    $env:PADDLE_COMPONENT_MODEL_DIR = $previousModelDirectory
    Pop-Location
}
