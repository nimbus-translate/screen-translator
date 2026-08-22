[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
    [string] $Version,

    [string] $OutputDirectory = "release",
    # Strictly below 200 MB in decimal bytes, matching release-facing size
    # claims rather than using the larger 200 MiB interpretation.
    [long] $MaximumInstallerBytes = 199999999,
    [switch] $SkipAppBuild,
    [switch] $SkipInstaller,
    [switch] $SkipChecksum,
    [switch] $SignWithReleaseCertificate
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-IsccPath {
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $knownPath = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    if (Test-Path -LiteralPath $knownPath) { return $knownPath }
    throw "找不到 Inno Setup 6（ISCC.exe）。先安装 Inno Setup，再构建安装包。"
}

$root = Split-Path -Parent $PSScriptRoot
$specPath = Join-Path $root "build-lite.spec"
$installerScript = Join-Path $root "installer\ScreenTranslator.iss"
$distDirectory = Join-Path $root "dist"
$applicationPath = Join-Path $distDirectory "ScreenTranslator-Lite.exe"
$outputPath = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $root $OutputDirectory }

if ($MaximumInstallerBytes -lt 1) { throw "MaximumInstallerBytes 必须大于 0。" }

Push-Location $root
$previousBuildVersion = $env:SCREEN_TRANSLATOR_BUILD_VERSION
try {
    if (-not $SkipAppBuild) {
        $env:SCREEN_TRANSLATOR_BUILD_VERSION = $Version
        & python -m PyInstaller --noconfirm --clean $specPath
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller 轻量版构建失败。" }
    }

    if (-not (Test-Path -LiteralPath $applicationPath -PathType Leaf)) {
        throw "找不到轻量主程序：$applicationPath"
    }
    if ($SkipInstaller) { return }

    New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
    $iscc = Get-IsccPath
    $compilerArguments = @(
        "/DMyAppVersion=$Version",
        "/DSourceDir=$distDirectory",
        "/DOutputDir=$outputPath"
    )
    if ($SignWithReleaseCertificate) {
        $signScript = Join-Path $root "scripts\sign_release.ps1"
        $signCommand = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$signScript`" -Path `$f"
        $compilerArguments += "/DEnableSigning=1"
        $compilerArguments += "/SScreenTranslator=$signCommand"
    }
    & $iscc @compilerArguments $installerScript
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup 安装包构建失败。" }

    $installerPath = Join-Path $outputPath "ScreenTranslator-Lite-$Version-Setup.exe"
    if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
        throw "Inno Setup 没有生成预期安装包：$installerPath"
    }
    $installerSize = (Get-Item -LiteralPath $installerPath).Length
    if ($installerSize -ge $MaximumInstallerBytes) {
        throw "轻量安装包大小为 $installerSize bytes，超过 $MaximumInstallerBytes bytes 上限。"
    }

    if (-not $SkipChecksum) {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installerPath).Hash.ToLowerInvariant()
        $sidecarPath = "$installerPath.sha256"
        "$hash *$([IO.Path]::GetFileName($installerPath))" | Set-Content -LiteralPath $sidecarPath -Encoding ascii -NoNewline
    }
}
finally {
    $env:SCREEN_TRANSLATOR_BUILD_VERSION = $previousBuildVersion
    Pop-Location
}
