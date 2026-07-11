$oldPath = "C:\Users\rosha\Documents\Codex\2026-07-08\can-you-write-a-script-to"
$newPath = "C:\Users\rosha\Documents\Codex\2026-07-08\finance-ledger-lab"

if (Test-Path -LiteralPath $newPath) {
    Write-Error "Target folder already exists: $newPath"
    exit 1
}

Rename-Item -LiteralPath $oldPath -NewName "finance-ledger-lab"
Write-Host "Renamed project folder to: $newPath"
