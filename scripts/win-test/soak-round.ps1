# One round of the Windows soak test, run in the test VM by scripts/win-test/soak.py: builds the home folder the
# round's spec describes, notes every file's SHA-256, sends it with CherryMove.exe (sometimes switching the app off
# part way, or changing files while it packs them) and uploads everything for the host to restore and check.
param([string]$Server = "http://10.0.2.2:8120")
$ErrorActionPreference = 'Stop'
$W = 'C:\soak'
$SoakHome = Join-Path $env:USERPROFILE 'SoakHome'

if (-not ("SoakFs5" -as [type])) {
    Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

public static class SoakFs5 {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CreateHardLinkW(string link, string target, IntPtr sa);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern byte CreateSymbolicLinkW(string link, string target, int flags);

    public static string L(string p) { return p.StartsWith(@"\\?\") ? p : @"\\?\" + p; }

    public static void Wipe(string p) {
        var d = new DirectoryInfo(L(p));
        if (!d.Exists) return;
        foreach (var e in d.EnumerateFileSystemInfos()) {
            if ((e.Attributes & FileAttributes.ReparsePoint) != 0) {
                if (e is DirectoryInfo) Directory.Delete(e.FullName);
                else { File.SetAttributes(e.FullName, FileAttributes.Normal); File.Delete(e.FullName); }
            } else if (e is DirectoryInfo) {
                Wipe(e.FullName);
            } else {
                File.SetAttributes(e.FullName, FileAttributes.Normal);
                File.Delete(e.FullName);
            }
        }
        File.SetAttributes(d.FullName, FileAttributes.Directory);
        Directory.Delete(d.FullName);
    }

    public static void Dir(string p) { Directory.CreateDirectory(L(p)); }

    // kind 0: random bytes, 1: text, 2: zeros
    public static void Write(string p, long size, int seed, int kind) {
        var r = new Random(seed);
        var buf = new byte[1 << 20];
        byte[] text = Encoding.UTF8.GetBytes("seed " + seed + ": notes about the move to the new computer. ");
        long pos = 0;
        using (var f = new FileStream(L(p), FileMode.Create, FileAccess.Write, FileShare.None)) {
            while (pos < size) {
                int n = (int)Math.Min(size - pos, buf.Length);
                if (kind == 0) r.NextBytes(buf);
                else if (kind == 1) for (int i = 0; i < n; i++) buf[i] = text[(pos + i) % text.Length];
                else Array.Clear(buf, 0, n);
                f.Write(buf, 0, n);
                pos += n;
            }
        }
    }

    public static void Stream(string p, string stream, string data) {
        File.WriteAllText(L(p) + ":" + stream, data);
    }

    public static void Times(string p, long filetime) {
        File.SetLastWriteTimeUtc(L(p), DateTime.FromFileTimeUtc(filetime));
    }

    public static void Attrs(string p, int attrs) { File.SetAttributes(L(p), (FileAttributes)attrs); }

    public static bool HardLink(string link, string target) { return CreateHardLinkW(L(link), L(target), IntPtr.Zero); }

    public static bool SymLink(string link, string target, bool dir) {
        return CreateSymbolicLinkW(L(link), target, (dir ? 1 : 0) | 2) != 0;  // 2: allowed without admin in developer mode
    }

    static string Hash(string p) {
        try {
            using (var f = new FileStream(p, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var sha = SHA256.Create()) {
                return BitConverter.ToString(sha.ComputeHash(f)).Replace("-", "").ToLowerInvariant();
            }
        } catch (IOException) { return "locked"; }
        catch (UnauthorizedAccessException) { return "denied"; }
    }

    // One line per entry: path inside root, d/f, attributes, size, last write (FILETIME, UTC), SHA-256.
    public static void Manifest(string root, string outPath) {
        var sb = new StringBuilder();
        Walk(new DirectoryInfo(L(root)), "", sb);
        File.WriteAllText(outPath, sb.ToString(), new UTF8Encoding(false));
    }

    static void Walk(DirectoryInfo d, string rel, StringBuilder sb) {
        foreach (var e in d.EnumerateFileSystemInfos()) {
            string r = rel == "" ? e.Name : rel + "\\" + e.Name;
            bool dir = e is DirectoryInfo;
            FileSystemInfo x = e;
            if (!dir) { x = new FileInfo(e.FullName); x.Refresh(); }  // the folder's listing can be out of date for hard links
            var a = x.Attributes;
            bool link = (a & FileAttributes.ReparsePoint) != 0;
            long size = dir ? 0 : ((FileInfo)x).Length;
            string sha = dir || link ? "" : Hash(e.FullName);
            sb.Append(r).Append('\t').Append(dir ? 'd' : 'f').Append('\t').Append((int)a).Append('\t').Append(size)
              .Append('\t').Append(x.LastWriteTimeUtc.ToFileTimeUtc()).Append('\t').Append(sha).Append('\n');
            if (dir && !link) Walk((DirectoryInfo)e, r, sb);
        }
    }
}
'@
}

function Put($path, $name) {
    curl.exe -sS --fail -T $path "$Server/up/$name" | Out-Null
    if ($LASTEXITCODE) { throw "upload failed ($LASTEXITCODE)" }
}

$spec = Invoke-RestMethod -UseBasicParsing "$Server/files/build/win-soak/spec.json"
$t0 = Get-Date

# What the last round may have left running, then a clean start.
Get-Process CherryMove -ErrorAction SilentlyContinue | Stop-Process -Force
foreach ($f in 'lockers.txt', 'busy.pid') {
    if (Test-Path "$W\$f") {
        Get-Content "$W\$f" | ForEach-Object { Stop-Process -Id ([int]$_) -Force -ErrorAction SilentlyContinue }
    }
}
Start-Sleep -Milliseconds 300
[SoakFs5]::Wipe($SoakHome)
foreach ($d in 'sticks', 'share', 'up') { [SoakFs5]::Wipe("$W\$d") }
Get-ChildItem $W -File -ErrorAction SilentlyContinue | Where-Object Name -ne 'CherryMove.exe' | Remove-Item -Force
New-Item -ItemType Directory -Force "$W\sticks", "$W\share", "$W\up" | Out-Null
foreach ($i in 1..([int]$spec.sticks)) { New-Item -ItemType Directory -Force "$W\sticks\$i" | Out-Null }
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\CherryMove" -ErrorAction SilentlyContinue

$exe = "$W\CherryMove.exe"
if (-not (Test-Path $exe) -or (Get-FileHash $exe).Hash -ne $spec.app_sha) {
    Invoke-WebRequest -UseBasicParsing "$Server/files/build/move/parts/CherryMove.exe" -OutFile $exe
}

# The home folder.
$failed = New-Object System.Collections.Generic.List[string]
[SoakFs5]::Dir($SoakHome)
foreach ($it in $spec.items) {
    $p = Join-Path $SoakHome $it.p
    try {
        switch ($it.t) {
            'd' { [SoakFs5]::Dir($p) }
            'f' { [SoakFs5]::Write($p, [long]$it.size, [int]$it.seed, [int]$it.kind) }
            'h' { if (-not [SoakFs5]::HardLink($p, (Join-Path $SoakHome $it.target))) { throw "no hard link" } }
            's' { if (-not [SoakFs5]::SymLink($p, $it.target, [bool]$it.dir)) { throw "no symlink" } }
            'j' { New-Item -ItemType Junction -Path $p -Target $it.target | Out-Null }
            'a' { [SoakFs5]::Stream($p, $it.stream, $it.data) }
        }
    } catch { $failed.Add("$($it.t) $($it.p): $($_.Exception.Message)") }
}
foreach ($it in $spec.items) {
    if ($it.t -eq 'f' -and $it.mtime) {
        try { [SoakFs5]::Times((Join-Path $SoakHome $it.p), [long]$it.mtime) } catch { $failed.Add("time $($it.p)") }
    }
}
foreach ($it in $spec.items) {
    if ($it.attrs) {
        try { [SoakFs5]::Attrs((Join-Path $SoakHome $it.p), [int]$it.attrs) } catch { $failed.Add("attrs $($it.p)") }
    }
}

# Files another program has open without sharing (Windows won't let anyone read them).
$lockers = @()
foreach ($rel in $spec.locked) {
    $ready = "$W\locked-$($lockers.Count).ready"
    $path = [SoakFs5]::L((Join-Path $SoakHome $rel))
    [IO.File]::WriteAllText("$W\locked-$($lockers.Count).path", $path, (New-Object Text.UTF8Encoding $false))
    $cmd = "`$p = [IO.File]::ReadAllText('$W\locked-$($lockers.Count).path'); " +
           "`$f = [IO.File]::Open(`$p, 'Open', 'ReadWrite', 'None'); New-Item '$ready' | Out-Null; " +
           "while (-not (Test-Path '$W\release')) { Start-Sleep -Milliseconds 200 }; `$f.Close()"
    $proc = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList '-NoProfile', '-Command', $cmd
    $lockers += $proc
    Add-Content "$W\lockers.txt" $proc.Id
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Path $ready) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }
}

[SoakFs5]::Manifest($SoakHome, "$W\up\manifest.tsv")
[IO.File]::WriteAllText("$W\up\failed.txt", ($failed -join "`n"), (New-Object Text.UTF8Encoding $false))

$sendArgs = @('send', '--only', 'other') + @($spec.args)
$killed = $null
if ($spec.kill_after) {
    $p = Start-Process $exe -ArgumentList $sendArgs -PassThru -WindowStyle Hidden
    $null = $p.Handle
    $killed = -not $p.WaitForExit([int]($spec.kill_after * 1000))
    if ($killed) { try { $p.Kill() } catch {}; $p.WaitForExit() }  # Kill fails if it finished just then
}

# Someone keeps using the PC while it packs.
$busy = $null
if ($spec.busy) {
    $spec.busy | Add-Member -NotePropertyName home -NotePropertyValue $SoakHome -Force
    [IO.File]::WriteAllText("$W\busy.json", ($spec.busy | ConvertTo-Json -Depth 5 -Compress), (New-Object Text.UTF8Encoding $false))
    $script = @'
$b = [IO.File]::ReadAllText('C:\soak\busy.json') | ConvertFrom-Json
$log = 'C:\soak\up\touched.txt'
$enc = New-Object Text.UTF8Encoding $false
Start-Sleep -Milliseconds $b.delay
foreach ($o in $b.ops) {
    $p = '\\?\' + (Join-Path $b.home $o.p)
    [IO.File]::AppendAllText($log, $o.p + "`n", $enc)
    if ($o.q) { [IO.File]::AppendAllText($log, $o.q + "`n", $enc) }
    try {
        switch ($o.op) {
            'append'  { $f = [IO.File]::Open($p, 'Append', 'Write', 'ReadWrite'); $f.Write((New-Object byte[] 4096), 0, 4096); $f.Close() }
            'rewrite' { [IO.File]::WriteAllText($p, "rewritten while sending") }
            'shrink'  { $f = [IO.File]::Open($p, 'Open', 'Write', 'ReadWrite'); $f.SetLength([Math]::Floor($f.Length / 2)); $f.Close() }
            'delete'  { [IO.File]::Delete($p) }
            'rename'  { [IO.File]::Move($p, '\\?\' + (Join-Path $b.home $o.q)) }
            'create'  { [IO.File]::WriteAllText($p, "made while sending") }
        }
    } catch {}
    Start-Sleep -Milliseconds $o.gap
}
'@
    [IO.File]::WriteAllText("$W\busy.ps1", $script)
    $busy = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList '-NoProfile', '-File', "$W\busy.ps1"
    Set-Content "$W\busy.pid" $busy.Id
}

$send = Start-Process $exe -ArgumentList $sendArgs -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "$W\up\out.json" -RedirectStandardError "$W\up\err.txt"
$null = $send.Handle  # or ExitCode stays empty
$finished = $send.WaitForExit(3 * 3600 * 1000)
if (-not $finished) { $send.Kill() }
if ($busy) { [void]$busy.WaitForExit(120000); if (-not $busy.HasExited) { $busy.Kill() } }
New-Item "$W\release" | Out-Null
foreach ($l in $lockers) { [void]$l.WaitForExit(10000) }
Remove-Item "$W\release", "$W\lockers.txt", "$W\busy.pid" -ErrorAction SilentlyContinue
if (-not (Test-Path "$W\up\touched.txt")) { New-Item "$W\up\touched.txt" | Out-Null }
$summary = @{ exit = $(if ($finished) { $send.ExitCode } else { 'timeout' }); killed = $killed
              seconds = [int]((Get-Date) - $t0).TotalSeconds }
[IO.File]::WriteAllText("$W\up\summary.json", ($summary | ConvertTo-Json -Compress))

tar.exe -cf "$W\round.tar" -C $W sticks share up
Put "$W\round.tar" "round.tar"
Remove-Item "$W\round.tar"
$summary | ConvertTo-Json -Compress
