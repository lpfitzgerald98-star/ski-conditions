# One-off helper: sync this session's ski-conditions changes to the public
# GitHub Pages repo (lpfitzgerald98-star/ski-conditions). Not run automatically --
# a cross-repo push needs to come from you, not the agent.
#
# Usage: run from anywhere, e.g.:
#   powershell -File C:\ClaudeProjects\projects\ski-conditions\scripts\push_public.ps1

$ErrorActionPreference = "Stop"
$src = "C:\ClaudeProjects\projects\ski-conditions"
$tmp = "$env:TEMP\ski-conditions-public-sync"

if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
git clone git@github.com:lpfitzgerald98-star/ski-conditions.git $tmp

# Mirror everything except .git and the git-ignored runtime/data dirs (the
# public repo's own workflow regenerates web/data and data/*.db on its own).
robocopy $src $tmp /MIR /XD ".git" "web\data" "data" /XF "*.pyc"

Set-Location $tmp
git add -A
git status --short
git commit -m "Sync from private ClaudeProjects: absolute Skiability headline, expandable forecast, zoom-dismiss card"
git push origin main

Write-Host ""
Write-Host "Pushed. If the site doesn't refresh within a few minutes, check the"
Write-Host "Actions tab on https://github.com/lpfitzgerald98-star/ski-conditions"
Write-Host "and trigger the build workflow manually if it didn't fire on push."
