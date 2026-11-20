# Creates (or resets) a small FastAPI project for the live VeriCode demo.
# Usage:  powershell -ExecutionPolicy Bypass -File demo\setup_demo.ps1 [-Path <dir>]
# Then open <dir> in Antigravity and use the prompt in demo\README.md.
param([string]$Path = (Join-Path $HOME "vericode-demo"))

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$marker = ".vericode-demo"

if (Test-Path $Path) {
    # Only ever delete a folder this script created.
    if (-not (Test-Path (Join-Path $Path $marker))) {
        throw "$Path exists and was not created by this script; pick another -Path."
    }
    Remove-Item -Recurse -Force $Path
}

New-Item -ItemType Directory -Force (Join-Path $Path ".agents\skills") | Out-Null
Copy-Item -Recurse (Join-Path $repo ".agents\skills\vericode-verify") (Join-Path $Path ".agents\skills\")

function Write-Utf8([string]$Rel, [string]$Text) {
    # WriteAllText writes UTF-8 without a BOM (Set-Content on PowerShell 5.1 adds one).
    [IO.File]::WriteAllText((Join-Path $Path $Rel), $Text.Replace("`r`n", "`n"))
}

Write-Utf8 $marker "Created by veri_code/demo/setup_demo.ps1. Safe to delete.`n"
Write-Utf8 ".gitignore" "$marker`n__pycache__/`n.venv/`n"
Write-Utf8 "requirements.txt" "fastapi==0.115.0`n"
Write-Utf8 "app.py" @'
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def get_user(token: str) -> dict[str, str]:
    # TODO: decode the token and return the user id
    raise NotImplementedError
'@

git -C $Path init -q
git -C $Path add -A
git -C $Path commit -q -m "Demo starting point"
Write-Host "Demo project ready: $Path"
