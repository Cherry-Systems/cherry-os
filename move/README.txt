Cherry Move: bring your files to Cherry OS
==========================================

Install Cherry Move on your OLD computer. It packs up your documents, photos, music, videos,
desktop, downloads and browser bookmarks, compressed and encrypted, onto a USB stick (or
several), your own Google Drive / OneDrive / MEGA, or a NAS at home. Nothing on the old
computer is changed or deleted. From a Mac's Photos library it brings the original photos and
videos that are on the Mac (not ones kept only in iCloud).

  Windows 10 or 11   Open CherryMove-Setup.exe and click Install. It goes in the Start menu
                     and on the desktop, and doesn't need an administrator. If Windows says
                     "Windows protected your PC" (the app isn't signed yet), click "More info",
                     then "Run anyway". To remove it later: Settings > Apps > Cherry Move.

  macOS 11 or newer  Open CherryMove.dmg and drag Cherry Move onto Applications. The first
                     time you open it, macOS blocks it because it isn't signed yet: open
                     System Settings > Privacy & Security, click "Open Anyway" near the
                     bottom and type your Mac's password (on macOS 14 or older you can also
                     right-click it and choose Open). When you click Get started, allow it to
                     look in your folders. To remove it later, drag it from Applications to
                     the Bin.

  Ubuntu, Debian,    Open cherry-move-app_..._amd64.deb (or _arm64.deb on ARM) in your software
  Mint               installer, or run: sudo apt install ./cherry-move-app_*_amd64.deb

  Fedora, openSUSE   sudo dnf install ./cherry-move-app-*.x86_64.rpm
                     (openSUSE: sudo zypper install ./cherry-move-app-*.x86_64.rpm)

  Another Linux      cherry-move-linux-x86_64 (or -arm64): make it executable and run it.
                     It opens in its own window if WebKitGTK is installed, otherwise in the
                     browser.

Then, on the NEW computer: plug the transfer USB stick in while installing Cherry OS (or any
time after), and your files come back by themselves. If you kept the files in your own cloud
or a NAS instead, open Cherry Move in Cherry OS, choose "Get files from my old computer", pick
"Google Drive, OneDrive or MEGA" or "A NAS or another computer at home", and type your code.
