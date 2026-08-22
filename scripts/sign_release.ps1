[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string] $Path,

    [string] $PfxBase64 = $env:WINDOWS_CERTIFICATE_PFX_BASE64,
    [string] $PfxPassword = $env:WINDOWS_CERTIFICATE_PASSWORD,
    [string] $TimestampUrl = "http://timestamp.digicert.com",
    [long] $MaximumFileSizeBytes = 0,
    [switch] $WriteSha256
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-SignToolPath {
    $command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $sdkRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path -LiteralPath $sdkRoot) {
        $candidate = Get-ChildItem -LiteralPath $sdkRoot -Directory |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
            Select-Object -First 1
        if ($candidate) { return $candidate }
    }
    throw "找不到 signtool.exe；请安装 Windows SDK。"
}

if ([string]::IsNullOrWhiteSpace($PfxBase64) -or [string]::IsNullOrWhiteSpace($PfxPassword)) {
    throw "缺少代码签名凭据：必须提供 WINDOWS_CERTIFICATE_PFX_BASE64 和 WINDOWS_CERTIFICATE_PASSWORD。"
}
if ($MaximumFileSizeBytes -lt 0) { throw "MaximumFileSizeBytes 不能小于 0。" }

$signTool = Get-SignToolPath

$certificatePath = Join-Path ([IO.Path]::GetTempPath()) ("screen-translator-signing-" + [guid]::NewGuid().ToString("N") + ".pfx")
try {
    try {
        [IO.File]::WriteAllBytes($certificatePath, [Convert]::FromBase64String($PfxBase64))
    }
    catch {
        throw "WINDOWS_CERTIFICATE_PFX_BASE64 不是有效的 Base64 PFX。"
    }

    & $signTool sign /fd SHA256 /f $certificatePath /p $PfxPassword /tr $TimestampUrl /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) { throw "signtool 签名失败：$Path" }
    & $signTool verify /pa /all $Path
    if ($LASTEXITCODE -ne 0) { throw "signtool 签名校验失败：$Path" }

    if ($MaximumFileSizeBytes -gt 0 -and (Get-Item -LiteralPath $Path).Length -ge $MaximumFileSizeBytes) {
        throw "签名后的文件超过大小上限：$Path"
    }
    if ($WriteSha256) {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
        "$hash *$([IO.Path]::GetFileName($Path))" | Set-Content -LiteralPath "$Path.sha256" -Encoding ascii -NoNewline
    }
}
finally {
    if (Test-Path -LiteralPath $certificatePath) {
        Remove-Item -LiteralPath $certificatePath -Force
    }
}
