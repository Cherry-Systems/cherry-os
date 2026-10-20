; Cherry Move's Windows installer. Installs for the current user only, so it never asks for an administrator.
; makensis -DVERSION=1.0 -DVERSION4=1.0.0.0 -DPARTS=<folder with the exes> -DART=<make_art.py output>
;          -DOUT=CherryMove-Setup.exe installer.nsi
Unicode true
ManifestDPIAware true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include MUI2.nsh
!include x64.nsh
!include LogicLib.nsh
!include FileFunc.nsh
!include WinMessages.nsh

!define APP "Cherry Move"
!define EXE "CherryMove.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\CherryMove"
!define WEBVIEW2 "Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

Name "${APP}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\Cherry Move"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
BrandingText "Cherry OS"

VIProductVersion "${VERSION4}"
VIFileVersion "${VERSION4}"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "CompanyName" "Cherry OS"
VIAddVersionKey "FileDescription" "Cherry Move Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Cherry OS"

!define MUI_ICON "${ART}\CherryMove.ico"
!define MUI_UNICON "${ART}\CherryMove.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${ART}\welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${ART}\welcome.bmp"
!define MUI_ABORTWARNING

!define MUI_WELCOMEPAGE_TITLE "Install Cherry Move"
!define MUI_WELCOMEPAGE_TEXT "Cherry Move packs up your files on this computer, compressed and encrypted, so Cherry OS can bring them back on your new one. Nothing on this computer is changed or deleted.$\r$\n$\r$\nIt's installed just for you, so you don't need an administrator password.$\r$\n$\r$\nClick Install to continue."
!define MUI_PAGE_CUSTOMFUNCTION_SHOW WelcomeShow
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "Cherry Move is installed"
!define MUI_FINISHPAGE_TEXT "You'll find it in the Start menu and on your desktop."
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Open Cherry Move now"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Function WelcomeShow
  GetDlgItem $0 $HWNDPARENT 1
  SendMessage $0 ${WM_SETTEXT} 0 "STR:&Install"
FunctionEnd

; The exe can't be replaced or removed while it's open.
!macro WaitUntilClosed
  ${Do}
    FindWindow $0 "webview" "${APP}"
    ${IfThen} $0 = 0 ${|} ${ExitDo} ${|}
    ${IfCmd} MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Cherry Move is open. Close it, then click Retry." /SD IDCANCEL IDCANCEL ${||} Abort ${|}
  ${Loop}
!macroend

Section
  !insertmacro WaitUntilClosed
  SetOutPath "$INSTDIR"
  ${If} ${IsNativeARM64}
    File "/oname=${EXE}" "${PARTS}\CherryMove-arm64.exe"
  ${Else}
    File "/oname=${EXE}" "${PARTS}\CherryMove.exe"
  ${EndIf}
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${EXE}"
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\${EXE}"

  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${EXE},0"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "Cherry OS"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" $0

  ; Windows 11 and most Windows 10 PCs have the web view Cherry Move's window uses. Without it the app opens
  ; in an Edge window instead, so a failed download here isn't a problem.
  ReadRegStr $0 HKLM "SOFTWARE\${WEBVIEW2}" "pv"
  ReadRegStr $1 HKCU "Software\${WEBVIEW2}" "pv"
  ${If} "$0$1" == ""
  ${OrIf} "$0$1" == "0.0.0.0"
    DetailPrint "Getting Microsoft Edge WebView2..."
    InitPluginsDir
    File "/oname=$PLUGINSDIR\MicrosoftEdgeWebview2Setup.exe" "${PARTS}\MicrosoftEdgeWebview2Setup.exe"
    ExecWait '"$PLUGINSDIR\MicrosoftEdgeWebview2Setup.exe" /silent /install'
  ${EndIf}
SectionEnd

Section "Uninstall"
  !insertmacro WaitUntilClosed
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APP}.lnk"
  Delete "$DESKTOP\${APP}.lnk"
  ; Its settings, the web view's data and any cloud sign-in it kept.
  RMDir /r "$LOCALAPPDATA\CherryMove"
  DeleteRegKey HKCU "${UNINST_KEY}"
SectionEnd
