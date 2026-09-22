$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$agentPython = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $agentPython = & py -3 -c "import sys; assert sys.version_info >= (3,10); print(sys.executable)" 2>$null
    if ($LASTEXITCODE -ne 0) { $agentPython = $null }
}
if (-not $agentPython -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $agentPython = & python -c "import sys; assert sys.version_info >= (3,10); print(sys.executable)" 2>$null
    if ($LASTEXITCODE -ne 0) { $agentPython = $null }
}
if (-not $agentPython) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'Python is required. The official download page will open; install Python and run INSTALL.bat again.'
        Start-Process 'https://www.python.org/downloads/windows/'
        exit 1
    }
    Write-Host 'Installing Python for your Windows user...'
    & winget install --id Python.Python.3.12 --exact --source winget --scope user --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Python could not be installed.' }
    $agentPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (-not (Test-Path $agentPython)) { throw 'Open INSTALL.bat again to locate Python.' }
}
& $agentPython install.py
exit $LASTEXITCODE
