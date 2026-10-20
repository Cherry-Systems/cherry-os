# Fills the test user's folders with files for Cherry Move to export, then prints their SHA-256s.
$ErrorActionPreference = 'Stop'
$rng = New-Object System.Random 42
$text = (1..3000 | ForEach-Object { "Line ${_}: notes about the move to the new computer, nothing secret here." }) -join "`r`n"

function Random-File($path, $mb) {
    New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
    $buf = New-Object byte[] ($mb * 1MB)
    $rng.NextBytes($buf)
    [IO.File]::WriteAllBytes($path, $buf)
}
function Text-File($path, $body) {
    New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
    [IO.File]::WriteAllText($path, $body)
}

$docs = [Environment]::GetFolderPath('MyDocuments')
$pics = [Environment]::GetFolderPath('MyPictures')
$music = [Environment]::GetFolderPath('MyMusic')
$videos = [Environment]::GetFolderPath('MyVideos')
$desk = [Environment]::GetFolderPath('Desktop')
$down = Join-Path $env:USERPROFILE 'Downloads'

Text-File "$docs\School report.txt" $text
Text-File "$docs\School report (1).txt" $text
Text-File "$docs\Café menu – winter.txt" "Flat white`r`nLong black`r`nBabycino"
Text-File "$docs\Projects\Robot arm\notes.md" "# Robot arm`r`nServo 3 is wired backwards."
Random-File "$docs\Tax 2025\statement.pdf" 3
for ($i = 1; $i -le 50; $i++) { Random-File ("$pics\Camera\IMG_{0:D4}.jpg" -f $i) 2 }
New-Item -ItemType Directory -Force "$pics\Holiday" | Out-Null
for ($i = 1; $i -le 5; $i++) { Copy-Item ("$pics\Camera\IMG_{0:D4}.jpg" -f $i) "$pics\Holiday\" }
Random-File "$music\Band - Song.mp3" 9
Random-File "$music\Band - Another song.mp3" 7
Random-File "$videos\Birthday.mp4" 30
Text-File "$desk\Shopping list.txt" "Milk`r`nEggs`r`nA bigger USB stick"
Random-File "$down\setup-tool.exe" 5
Text-File "$env:USERPROFILE\.ssh\id_ed25519" "-----BEGIN OPENSSH PRIVATE KEY-----`nnot a real key`n-----END OPENSSH PRIVATE KEY-----`n"
Text-File "$env:USERPROFILE\.gitconfig" "[user]`n`tname = Alex`n"
Text-File "$env:USERPROFILE\Code\hello\hello.py" "print('hello from the old computer')`n"

$roots = @{ DOCUMENTS = $docs; PICTURES = $pics; MUSIC = $music; VIDEOS = $videos; DESKTOP = $desk; DOWNLOAD = $down }
foreach ($k in $roots.Keys) {
    Get-ChildItem -Recurse -File $roots[$k] | Where-Object { $_.Name -ne 'desktop.ini' } | ForEach-Object {
        $rel = $_.FullName.Substring($roots[$k].Length + 1).Replace('\', '/')
        "{0}  {1}/{2}" -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLower(), $k, $rel
    }
}
foreach ($rel in '.ssh\id_ed25519', '.gitconfig', 'Code\hello\hello.py') {
    "{0}  HOME/{1}" -f (Get-FileHash -Algorithm SHA256 "$env:USERPROFILE\$rel").Hash.ToLower(), $rel.Replace('\', '/')
}
