# PhonePad

Turn your Android phone into a PS5-style gamepad for your Windows PC. Two apps, no servers, no internet:

- **PhonePad** (Android): full-screen DualSense-style controller with two sticks, D-pad, ✕ ○ □ △, L1/R1, analog L2/R2, Create/Options/PS, touchpad button, and gyro aiming.
- **PhonePad PC** (Windows): receives your phone's input directly over Wi-Fi and shows up to games as a virtual Xbox 360 controller.

Both apps talk straight to each other over your local network (UDP). Nothing goes through the internet.

## Get the apps

Push this repo to GitHub. The **Build apps** workflow (Actions tab) produces:

- `PhonePad-Android` → `app-debug.apk` (install on the phone; allow "install unknown apps" once)
- `PhonePad-Windows` → `PhonePad-PC.exe`

## Use it

1. **One time on the PC:** install the [ViGEmBus driver](https://github.com/nefarius/ViGEmBus/releases) (this is what creates the virtual controller).
2. Run `PhonePad-PC.exe`. If Windows Firewall asks, allow access on **private networks**.
3. Put the phone and PC on the same network. A Wi-Fi router works, and so does a phone hotspot or the Windows Mobile Hotspot with no data connection.
4. Open **PhonePad** on the phone, tap **Find my PC**, then **Connect**. If the PC isn't found, type the address shown in the PC app.
5. Tap **GYRO** for tilt-to-aim on the right stick. Press Back on the phone to return to the connect screen.

## Customize your layout

1. On the controller screen, **press and hold the small ✎ tab** at the top centre (hold ~½ s, so it can't trigger mid-game).
2. **Move:** drag any button, stick, trigger or the touchpad where you want it (it stays on screen).
3. **Resize:** select a control, then drag the round ⤡ handle on its corner, or use the size slider. The **Whole pad** slider scales everything.
4. **Shell** hides the grey controller body if you want a free-form layout. **Reset control** / **Reset all** undo changes. ⇅ moves the toolbar out of your way.
5. Everything **auto-saves to the default profile** and loads every time the controller opens. Tap **Done** to go back to playing.

## Button mapping (virtual Xbox 360 pad)

✕=A, ○=B, □=X, △=Y, L1/R1=LB/RB, L2/R2=LT/RT (analog: touch high = light pull, slide down = full), Create/touchpad=Back, Options=Start, PS=Guide, tap a stick=L3/R3.

## Project structure

```
android/                          Android app (Java, no dependencies)
  app/src/main/java/.../MainActivity.java   connect screen, UDP sender, gyro, profile storage
  app/src/main/assets/index.html            controller UI + layout editor (bundled, works offline)
pc/phonepad_pc.py                 Windows app (Tk window + UDP listener + vgamepad)
.github/workflows/build.yml       builds the APK and the EXE
```

## Build locally

- Android: open `android/` in Android Studio, or run `gradle -p android assembleDebug`
- PC: `pip install -r pc/requirements.txt pyinstaller` then `pyinstaller --onefile --windowed --name PhonePad-PC --collect-all vgamepad pc/phonepad_pc.py`

## Notes

- If gyro aiming is inverted, flip the sign of `GYRO_X` / `GYRO_Y` in `assets/index.html`.
- Anyone on the same network who knows the PC's address can send input to the PC app, so use trusted networks.
