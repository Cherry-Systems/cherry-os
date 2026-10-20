# Test helper inside the throwaway Windows VM: runs PowerShell snippets queued by scripts/win-test/winvm.py
# on the host (reachable from the VM as 10.0.2.2) and posts back their output.
$server = "http://10.0.2.2:8120"
while ($true) {
    try {
        $job = Invoke-RestMethod -Uri "$server/next" -TimeoutSec 30
        if ($job -and $job.id) {
            try {
                $out = Invoke-Expression $job.script 2>&1 | Out-String -Width 200
            } catch {
                $out = "ERROR: " + ($_ | Out-String)
            }
            $body = @{ id = $job.id; output = $out } | ConvertTo-Json -Compress
            Invoke-RestMethod -Uri "$server/done" -Method Post -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json; charset=utf-8" | Out-Null
            continue
        }
    } catch {}
    Start-Sleep -Seconds 2
}
