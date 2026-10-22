# Building the standalone app

PyInstaller builds for the system it runs on: build the `.exe` on Windows, the `.app` / `.dmg` on macOS.
The GitHub workflow `.github/workflows/release.yml` does both when you push a tag (`git tag v2.6.0 && git push --tags`)
and attaches the files to the release.

## Build locally

```bash
python3 -m venv .venv-build
source .venv-build/bin/activate           
pip install pyinstaller
pyinstaller --clean --noconfirm pos_erp.spec
```

Result: `dist/EnterprisePOS.app` (macOS) or `dist/EnterprisePOS.exe` (Windows).

macOS disk image:

```bash
hdiutil create -volname "Enterprise POS" -srcfolder dist/EnterprisePOS.app -ov -format UDZO dist/EnterprisePOS.dmg
```

Windows: zip the exe (`Compress-Archive -Path dist\EnterprisePOS.exe -DestinationPath EnterprisePOS-windows.zip`).

## Where the data lives

A packaged app never writes next to itself. On first run it creates its folder and the SQLite database:

| System | Folder |
|---|---|
| macOS | `~/Library/Application Support/EnterprisePOS/` |
| Windows | `%APPDATA%\EnterprisePOS\` |
| Linux | `~/.local/share/enterprise-pos` style `EnterprisePOS/` under `$XDG_DATA_HOME` |

It holds `general_inventory.db`, `backups/`, `invoices/`, `logs/` and (optionally) `mail.env`. Set `POS_DATA_DIR` to
move all of it. To test a first run, quit the app and delete that folder.

## Notes

- Use a Python from python.org (it ships Tk). Some Homebrew Pythons have no working Tk, and a brand-new Python
  release may not be supported by PyInstaller yet: if the build fails, try Python 3.12.
- The apps are not code-signed. macOS: right-click the app > Open the first time. Windows SmartScreen: More info > Run anyway.
- The macOS build made on GitHub runs on Apple-silicon Macs only. Build locally on an Intel Mac for Intel.
- Debug build: change `console=False` to `console=True` in `pos_erp.spec` to see errors in a terminal.
- Optional icons: `assets/icon.icns` (macOS) and `assets/icon.ico` (Windows) are picked up automatically.