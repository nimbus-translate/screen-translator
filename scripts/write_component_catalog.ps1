[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Container })]
    [string] $ComponentDirectory,

    [string] $OutputPath = "",

    [switch] $VerifyExisting
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$componentRoot = (Resolve-Path -LiteralPath $ComponentDirectory).Path.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$catalogPath = if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    Join-Path $componentRoot "component-files.json"
} else {
    [System.IO.Path]::GetFullPath($OutputPath)
}
$expectedCatalogPath = [System.IO.Path]::GetFullPath((Join-Path $componentRoot "component-files.json"))
if (-not $catalogPath.Equals($expectedCatalogPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw "组件文件目录必须写入组件根目录的 component-files.json。"
}
$catalogSignaturePath = Join-Path $componentRoot "component-files.p7s"
if ($VerifyExisting) {
    if (-not (Test-Path -LiteralPath $catalogPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $catalogSignaturePath -PathType Leaf)) {
        throw "缺少待复验的 component-files.json 或 component-files.p7s。"
    }
} elseif (Test-Path -LiteralPath $catalogSignaturePath) {
    throw "已有 component-files.p7s，拒绝生成与旧签名不匹配的新目录。"
}

$rootPrefix = $componentRoot + [System.IO.Path]::DirectorySeparatorChar
$excluded = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
[void]$excluded.Add("component-files.json")
[void]$excluded.Add("component-files.p7s")
[void]$excluded.Add(".component-manifest.json")
$relativePaths = [Collections.Generic.List[string]]::new()
$filesByPath = @{}

foreach ($item in Get-ChildItem -LiteralPath $componentRoot -Force -Recurse) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "组件目录不能包含链接或重解析点：$($item.FullName)"
    }
    if ($item.PSIsContainer) { continue }
    $fullPath = [IO.Path]::GetFullPath($item.FullName)
    if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "组件文件越出组件根目录：$fullPath"
    }
    $relativePath = $fullPath.Substring($rootPrefix.Length).Replace("\", "/")
    if ($excluded.Contains($relativePath)) { continue }
    if ([string]::IsNullOrWhiteSpace($relativePath) -or
        $relativePath.StartsWith("/") -or
        $relativePath.Contains("\") -or
        $relativePath.Contains(":") -or
        $relativePath.Split("/") -contains ".." -or
        $relativePath.Split("/") -contains ".") {
        throw "组件文件相对路径不安全：$relativePath"
    }
    if ($filesByPath.ContainsKey($relativePath)) {
        throw "组件目录包含大小写冲突路径：$relativePath"
    }
    $filesByPath[$relativePath] = $fullPath
    $relativePaths.Add($relativePath)
}

if ($relativePaths.Count -eq 0) { throw "组件目录没有可签名文件。" }
if ($relativePaths.Count -gt 100000) { throw "组件文件数量异常。" }
$relativePaths.Sort([StringComparer]::Ordinal)

$records = [Collections.Generic.List[object]]::new()
[long]$totalSize = 0
foreach ($relativePath in $relativePaths) {
    $file = Get-Item -LiteralPath $filesByPath[$relativePath]
    $totalSize += [long]$file.Length
    if ($totalSize -gt 8GB) { throw "组件文件总大小异常。" }
    $records.Add([ordered]@{
        path = $relativePath
        size = [long]$file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    })
}

$catalog = [ordered]@{
    schema_version = 1
    files = $records
} | ConvertTo-Json -Depth 4 -Compress
$encoding = [Text.UTF8Encoding]::new($false)
if ($VerifyExisting) {
    $expectedBytes = $encoding.GetBytes($catalog)
    $existingBytes = [IO.File]::ReadAllBytes($catalogPath)
    if ($expectedBytes.Length -ne $existingBytes.Length -or
        [Convert]::ToBase64String($expectedBytes) -ne [Convert]::ToBase64String($existingBytes)) {
        throw "组件在文件目录签名后发生变化，拒绝归档。"
    }
} else {
    [IO.File]::WriteAllText($catalogPath, $catalog, $encoding)
}
