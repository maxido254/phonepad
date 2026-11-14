# PCStation Gamepad

Turn Android phones into PS5-style gamepads for your Windows PC, **up to 4 at once** for local multiplayer with friends. Two apps, no servers, no internet:

- **PCStation Gamepad** (Android): full-screen DualSense-style controller with two sticks, D-pad, ✕ ○ □ △, L1/R1, analog L2/R2, Create/Options/PS, touchpad button, gyro aiming, and a customizable layout.
- **PCStation Gamepad PC** (Windows): receives every phone's input directly over Wi-Fi and shows each one to games as its own virtual Xbox 360 controller (Player 1 to 4).

Both apps talk straight to each other over your local network (UDP). Nothing goes through the internet.

## Get the apps

Push this repo to GitHub. The **Build apps** workflow (Actions tab) produces:

- `PCStation-Gamepad-Android` → `app-debug.apk` (install on every phone; allow "install unknown apps" once)
- `PCStation-Gamepad-Windows` → `PCStation-Gamepad.exe`

## Use it

1. **One time on the PC:** install the [ViGEmBus driver](https://github.com/nefarius/ViGEmBus/releases) (this is what creates the virtual controller).
2. Run `PCStation-Gamepad.exe`. If Windows Firewall asks, allow access on **private networks**.
3. Put the phone and PC on the same network. A Wi-Fi router works, and so does a phone hotspot or the Windows Mobile Hotspot with no data connection.
4. Open **PCStation Gamepad** on the phone, tap **Find my PC**, then **Connect**. If the PC isn't found, type the address shown in the PC app.
5. **Multiplayer:** repeat step 4 on up to 4 phones. Each phone gets the next free player slot (the PC app lists them), and its top status bar shows **Player 1 to 4** with its colour (P1 blue, P2 red, P3 green, P4 pink). A fifth phone sees "PC full (4/4)" until someone leaves. A phone that drops and reconnects gets its own slot back.
6. Tap **GYRO** for tilt-to-aim on the right stick. Press Back on the phone to return to the connect screen.

## Customize your layout

1. On the controller screen, **press and hold the small ✎ tab** at the top centre (hold ~½ s, so it can't trigger mid-game).
2. **Move:** drag any button, stick, trigger or the touchpad where you want it (it stays on screen).
3. **Resize:** select a control, then drag the round ⤡ handle on its corner, or use the size slider. The **Whole pad** slider scales everything.
4. **Shell** hides the grey controller body if you want a free-form layout. **Reset control** / **Reset all** undo changes. ⇅ moves the toolbar out of your way.
5. Everything **auto-saves to the default profile** and loads every time the controller opens. Tap **Done** to go back to playing.

## Multiplayer notes

- The limit is 4 because games read Xbox 360 pads through XInput, which only supports 4 controllers. `MAX_PLAYERS` in `pc/pcstation_gamepad.py` is the single setting if you ever need to change it.
- Every phone saves its own layout profile, so friends can each arrange their own controller.
- A phone that goes quiet has its buttons released after 3 s and its slot freed after 20 s.
- Games that expect Player 1 to be the first controller will see the first phone to connect as Player 1.

## Button mapping (virtual Xbox 360 pad)

✕=A, ○=B, □=X, △=Y, L1/R1=LB/RB, L2/R2=LT/RT (analog: touch high = light pull, slide down = full), Create/touchpad=Back, Options=Start, PS=Guide, tap a stick=L3/R3.

## Project structure

```
android/                          Android app (Java, no dependencies)
  app/src/main/java/.../MainActivity.java   connect screen, UDP sender, gyro, profile storage
  app/src/main/assets/index.html            controller UI + layout editor (bundled, works offline)
pc/pcstation_gamepad.py           Windows app (Tk window + UDP listener + up to 4 vgamepad pads)
.github/workflows/build.yml       builds the APK and the EXE
```

## Build locally

- Android: open `android/` in Android Studio, or run `gradle -p android assembleDebug`
- PC: `pip install -r pc/requirements.txt pyinstaller` then `pyinstaller --onefile --windowed --name PCStation-Gamepad --collect-all vgamepad pc/pcstation_gamepad.py`

## Notes

- If gyro aiming is inverted, flip the sign of `GYRO_X` / `GYRO_Y` in `assets/index.html`.
- Anyone on the same network who knows the PC's address can send input to the PC app, so use trusted networks.
