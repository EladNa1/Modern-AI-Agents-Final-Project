# Exports the architecture diagram used by the app + /api/model_architecture.
# The SINGLE SOURCE OF TRUTH is slide 7 of CheckMate.pptx ("CheckMate as a
# Reflection Agent"), so the site diagram stays 1-to-1 with the deck.
# Requires PowerPoint (COM). Run from the repo root:  powershell -File scripts/export_architecture.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src  = Join-Path $root "presentation\CheckMate.pptx"
$out  = Join-Path $root "public\architecture.png"
$slideNumber = 7

$ppt = New-Object -ComObject PowerPoint.Application
try {
  $pres = $ppt.Presentations.Open($src, $true, $false, $false)  # ReadOnly, Untitled, no window
  $w = [int]($pres.PageSetup.SlideWidth * 2)   # 2x for a crisp raster
  $h = [int]($pres.PageSetup.SlideHeight * 2)
  $pres.Slides.Item($slideNumber).Export($out, "PNG", $w, $h)
  $pres.Close()
  Write-Output ("wrote " + $out + " (" + $w + "x" + $h + ") from slide " + $slideNumber)
} finally {
  $ppt.Quit()
  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($ppt) | Out-Null
}
