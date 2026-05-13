Omega Runtime Studio — portable bundle layout
===============================================

Place next to the executables:

  models\     Optional GGUF / weights (or use UI Downloads / registry paths).
  vendor\     Harvested accelerator binaries under vendor\accelerators\bin
              (legacy trees may use vendor\lemonade\bin — runtime falls back if needed).
              Sync: .\scripts\sync_vendor_accelerators.ps1 -VendorDest <this folder>
  python\     Optional embedded CPython (python-embed-amd64) + site-packages; see repo README "Production bundle".
              When present, Omega3.0-portable-Server.exe "serve" re-execs via python\python.exe for full stdlib.

Set OMEGA_BUNDLE_ROOT to this directory when running headless so the API resolves vendor\models.

CLI (tray-less server), one line:

  Omega3.0-portable-Server.exe daemon --port 11434

Or use run-server.bat (prefers .\python\python.exe -m omega_studio.cli serve when python\ exists).

(Older bundles may ship `omega3-portable.exe`; both work next to the GUI.)

Logs default under %LOCALAPPDATA%\Omega3Portable\ unless --log-file is set.

GUI (operators): Omega3.0-portable.exe
