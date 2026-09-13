$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$healthUrl = "http://127.0.0.1:8765/health"
$appUrl = "http://127.0.0.1:8765"
$logDirectory = Join-Path $projectRoot "data\logs"
$standardLog = Join-Path $logDirectory "backend.log"
$errorLog = Join-Path $logDirectory "backend-error.log"

function Show-StartupError([string]$message) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        $message,
        "Sportsbeams Payroll Agent",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

try {
    $alreadyRunning = $false
    try {
        Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1 | Out-Null
        $alreadyRunning = $true
    } catch { }

    if (-not $alreadyRunning) {
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $pythonCommand = Get-Command python -ErrorAction Stop
        $backendProcess = Start-Process `
            -FilePath $pythonCommand.Source `
            -ArgumentList "-m", "app.api" `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $standardLog `
            -RedirectStandardError $errorLog `
            -PassThru

        $ready = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            Start-Sleep -Milliseconds 500
            if ($backendProcess.HasExited) { break }
            try {
                Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1 | Out-Null
                $ready = $true
                break
            } catch { }
        }
        if (-not $ready) {
            $details = if (Test-Path $errorLog) { (Get-Content -Raw $errorLog).Trim() } else { "No error log was produced." }
            throw "The local service did not start.`n`n$details`n`nLog: $errorLog"
        }
    }

    Start-Process -FilePath $appUrl
} catch {
    Show-StartupError $_.Exception.Message
    exit 1
}
