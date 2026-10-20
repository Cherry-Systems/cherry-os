# Helpers for testing the installed Cherry Move in the Windows test VM. Load with:
#   iwr http://10.0.2.2:8120/files/scripts/win-test/cherry-move.ps1 -UseBasicParsing -OutFile $env:TEMP\cm.ps1; . $env:TEMP\cm.ps1
Add-Type -AssemblyName System.Web
if (-not ("CherryTest.User32" -as [type])) {
    Add-Type -Name User32 -Namespace CherryTest -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
'@
}

# The app's window (0 if it isn't open).
function CM-Window {
    $p = Get-Process CherryMove -ErrorAction SilentlyContinue | Where-Object MainWindowHandle -ne 0 | Select-Object -First 1
    if ($p) { $p.MainWindowHandle } else { [IntPtr]::Zero }
}

# Calls the running app's API, like its page does.
function CM-Api($path, $body) {
    $info = Get-Content "$env:LOCALAPPDATA\CherryMove\running.json" | ConvertFrom-Json
    $u = [Uri]$info.url
    $headers = @{ "X-Cherry" = [Web.HttpUtility]::ParseQueryString($u.Query)["t"] }
    $base = "http://127.0.0.1:$($u.Port)"
    if ($null -eq $body) { return Invoke-RestMethod "$base$path" -Headers $headers }
    Invoke-RestMethod "$base$path" -Method Post -Headers $headers -ContentType "application/json" `
        -Body ($body | ConvertTo-Json -Compress -Depth 5)
}

# Clicks the window's close button (WM_CLOSE).
function CM-Close { [CherryTest.User32]::PostMessage((CM-Window), 0x10, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null }
