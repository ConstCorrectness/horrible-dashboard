; NSIS hooks for electron-builder (electron-builder.json → nsis.include).
;
; Moving off the Tauri shell: versions up to 0.4.4 were Tauri builds, installed by
; Tauri's own NSIS installer into %LOCALAPPDATA%\horrible-dashboard and registered
; under HKCU\...\Uninstall\horrible-dashboard (Tauri keys it by product name). This
; installer puts the Electron build in %LOCALAPPDATA%\Programs\horrible-dashboard
; under a GUID key, so without this hook a user who updates from the Tauri app ends
; up with both: two Start-menu entries, two uninstallers, and an old app that still
; starts its own backend.
;
; So once our files are in place, the Tauri install (if any) is removed silently.
; Its uninstaller keeps the user's data in silent mode (Tauri only deletes app data
; when the "delete data" box is ticked, and /S never ticks it) — and the backend's
; data lives in HORRIBLE_DATA_DIR anyway, which no installer touches.
!macro customInstall
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\horrible-dashboard" "InstallLocation"
  ${If} $0 != ""
    ; Tauri writes the location quoted.
    StrCpy $1 $0 1
    ${If} $1 == '"'
      StrCpy $0 $0 "" 1
      StrCpy $0 $0 -1
    ${EndIf}
    ${If} ${FileExists} "$0\uninstall.exe"
      DetailPrint "Removing the previous (Tauri) version from $0"
      StrCpy $2 "0"
      ${If} ${FileExists} "$DESKTOP\${SHORTCUT_NAME}.lnk"
        StrCpy $2 "1"
      ${EndIf}
      ; `_?=` runs the uninstaller in place, so ExecWait really waits for it.
      ExecWait '"$0\uninstall.exe" /S _?=$0'
      Delete "$0\uninstall.exe"
      RMDir "$0"
      ; Tauri named its shortcuts after the product, as electron-builder does, so its
      ; uninstaller just deleted ours as well. Put them back.
      CreateShortCut "$SMPROGRAMS\${SHORTCUT_NAME}.lnk" "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
      WinShell::SetLnkAUMI "$SMPROGRAMS\${SHORTCUT_NAME}.lnk" "${APP_ID}"
      ${If} $2 == "1"
        CreateShortCut "$DESKTOP\${SHORTCUT_NAME}.lnk" "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
        WinShell::SetLnkAUMI "$DESKTOP\${SHORTCUT_NAME}.lnk" "${APP_ID}"
      ${EndIf}
    ${EndIf}
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\horrible-dashboard"
  ${EndIf}
!macroend
