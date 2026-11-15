<#
.SYNOPSIS
    Download the Geofabrik Turkiye (Turkey) OpenStreetMap extract (~620 MB) into ./data.

.DESCRIPTION
    Downloads turkey-latest.osm.pbf, verifies its MD5 checksum and records the
    server's Last-Modified date in turkey-latest.osm.pbf.timestamp (fallback for the
    extract date if the PBF header has none). The file is git-ignored and mounted into the qgis
    container as a volume (OSM_PBF_PATH=/data/turkey-latest.osm.pbf).

.EXAMPLE
    .\scripts\download_extract.ps1
    .\scripts\download_extract.ps1 -Force   # re-download even if the file exists
#>
param(
    [string]$Url = "https://download.geofabrik.de/europe/turkey-latest.osm.pbf",
    [string]$DataDir = (Join-Path $PSScriptRoot "..\data"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # the progress bar slows Invoke-WebRequest down a lot

$fileName = Split-Path $Url -Leaf
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$target = Join-Path (Resolve-Path $DataDir) $fileName
$part = "$target.part"
$stampFile = "$target.timestamp"

if ((Test-Path $target) -and -not $Force) {
    Write-Host "Already present: $target (use -Force to re-download)"
    exit 0
}

$headers = @{ "User-Agent" = "qgis-property-report/0.2 (extract download script)" }

Write-Host "Fetching checksum: $Url.md5"
$md5Text = (Invoke-WebRequest -Uri "$Url.md5" -Headers $headers -UseBasicParsing).Content
if ($md5Text -is [byte[]]) { $md5Text = [Text.Encoding]::ASCII.GetString($md5Text) }
$expected = ($md5Text -split "\s+")[0].ToLower()

Write-Host "Downloading $Url (~620 MB) ..."
$sw = [Diagnostics.Stopwatch]::StartNew()
$resp = Invoke-WebRequest -Uri $Url -Headers $headers -OutFile $part -PassThru -UseBasicParsing
Write-Host ("Downloaded in {0:N0} s" -f $sw.Elapsed.TotalSeconds)

$actual = (Get-FileHash -Path $part -Algorithm MD5).Hash.ToLower()
if ($actual -ne $expected) {
    Remove-Item $part -Force
    throw "MD5 mismatch (expected $expected, got $actual). Download removed; please retry."
}
Move-Item -Force $part $target

# Record the extract date (Last-Modified of the file on Geofabrik), as ISO 8601 UTC.
$lastModified = $resp.Headers["Last-Modified"]
if ($lastModified -is [array]) { $lastModified = $lastModified[0] }
if ($lastModified) {
    $date = [DateTime]::Parse($lastModified).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Set-Content -Path $stampFile -Value $date -Encoding ascii
    Write-Host "Extract date: $date (saved to $stampFile)"
} else {
    # No guessing: without a sidecar, the date is read from the PBF header.
    Remove-Item $stampFile -ErrorAction SilentlyContinue
    Write-Host "No Last-Modified header; the extract date will be read from the PBF header."
}

$sizeMb = [Math]::Round((Get-Item $target).Length / 1MB)
Write-Host "OK: $target ($sizeMb MB, MD5 verified)"
