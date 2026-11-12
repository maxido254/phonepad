"""PhonePad PC: receives your phone's controller input over Wi-Fi and presents it
to Windows as a virtual Xbox 360 gamepad. Double-click, then open the PhonePad app on your phone.
Works fully offline on any shared network (home Wi-Fi, phone hotspot, PC hotspot).
"""
import json
import socket
import threading
import time
import tkinter as tk

PORT = 8765
WATCHDOG_SECONDS = 3  # release all inputs if the phone goes quiet

try:
    import vgamepad as vg
    B = vg.XUSB_BUTTON
    BUTTONS = {
        "A": B.XUSB_GAMEPAD_A, "B": B.XUSB_GAMEPAD_B,
        "X": B.XUSB_GAMEPAD_X, "Y": B.XUSB_GAMEPAD_Y,
        "LB": B.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": B.XUSB_GAMEPAD_RIGHT_SHOULDER,
        "START": B.XUSB_GAMEPAD_START, "BACK": B.XUSB_GAMEPAD_BACK,
        "CREATE": B.XUSB_GAMEPAD_BACK, "OPTIONS": B.XUSB_GAMEPAD_START,
        "TOUCHPAD": B.XUSB_GAMEPAD_BACK, "PS": B.XUSB_GAMEPAD_GUIDE,
        "L3": B.XUSB_GAMEPAD_LEFT_THUMB, "R3": B.XUSB_GAMEPAD_RIGHT_THUMB,
        "UP": B.XUSB_GAMEPAD_DPAD_UP, "DOWN": B.XUSB_GAMEPAD_DPAD_DOWN,
        "LEFT": B.XUSB_GAMEPAD_DPAD_LEFT, "RIGHT": B.XUSB_GAMEPAD_DPAD_RIGHT,
    }
    IMPORT_ERROR = None
except Exception as exc:  # vgamepad / ViGEmBus not available
    vg = None
    BUTTONS = {}
    IMPORT_ERROR = str(exc)

pad = None
pad_error = IMPORT_ERROR
last_seen = 0.0
lock = threading.Lock()


def clamp(v, lo, hi):
    return max(lo, min(hi, float(v)))


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "unknown"
    finally:
        s.close()


def make_pad():
    global pad, pad_error
    if vg is None:
        return
    try:
        pad = vg.VX360Gamepad()
        pad_error = None
    except Exception as exc:
        pad_error = str(exc)


def apply(d):
    if pad is None:
        return
    kind = d.get("t")
    if kind == "b":
        btn = BUTTONS.get(d.get("b"))
        if btn is not None:
            if d.get("d"):
                pad.press_button(button=btn)
            else:
                pad.release_button(button=btn)
    elif kind == "s":
        x, y = clamp(d.get("x", 0), -1, 1), clamp(d.get("y", 0), -1, 1)
        if d.get("s") == "L":
            pad.left_joystick_float(x_value_float=x, y_value_float=y)
        else:
            pad.right_joystick_float(x_value_float=x, y_value_float=y)
    elif kind == "t":
        v = clamp(d.get("v", 0), 0, 1)
        if d.get("k") == "LT":
            pad.left_trigger_float(value_float=v)
        else:
            pad.right_trigger_float(value_float=v)
    pad.update()


def release_all():
    if pad is not None:
        pad.reset()
        pad.update()


def listen():
    global last_seen
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", PORT))
    while True:
        data, addr = sock.recvfrom(2048)
        if data.startswith(b"PHONEPAD?"):      # phone is looking for this PC
            sock.sendto(b"PHONEPAD!", addr)
        elif data.startswith(b"PING"):         # keep-alive
            last_seen = time.time()
            sock.sendto(b"PONG", addr)
        elif data.startswith(b"BYE"):          # phone left the controller screen
            with lock:
                release_all()
            last_seen = 0.0
        else:
            try:
                d = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            last_seen = time.time()
            with lock:
                apply(d)


def main():
    make_pad()
    try:
        threading.Thread(target=listen, daemon=True).start()
        port_error = None
    except Exception as exc:
        port_error = str(exc)

    root = tk.Tk()
    root.title("PhonePad PC")
    root.geometry("380x230")
    root.resizable(False, False)
    root.configure(bg="#0f1318")

    def label(text, size=11, color="#e6edf3", bold=False):
        w = tk.Label(root, text=text, bg="#0f1318", fg=color, wraplength=350, justify="center",
                     font=("Segoe UI", size, "bold" if bold else "normal"))
        w.pack(pady=4)
        return w

    label("PhonePad PC", 18, bold=True)
    label(f"This PC's address: {local_ip()}", 11, "#9fb0c3")
    state = label("", 12, bold=True)
    note = label("", 9, "#9fb0c3")

    def refresh():
        with lock:
            if pad is not None and last_seen and time.time() - last_seen > WATCHDOG_SECONDS:
                release_all()
        if pad_error:
            state.config(text="Virtual gamepad unavailable", fg="#f85149")
            note.config(text="Install the ViGEmBus driver (github.com/nefarius/ViGEmBus/releases), "
                             "then restart this app.\n" + pad_error[:120])
        elif port_error:
            state.config(text="Could not open port", fg="#f85149")
            note.config(text=port_error)
        elif last_seen and time.time() - last_seen <= WATCHDOG_SECONDS:
            state.config(text="Phone connected", fg="#3fb950")
            note.config(text="Your phone is now an Xbox 360 controller.")
        else:
            state.config(text="Waiting for phone…", fg="#d29922")
            note.config(text="Open PhonePad on your phone (same Wi-Fi) and tap “Find my PC”.\n"
                             "If Windows asks, allow access on private networks.")
        root.after(500, refresh)

    refresh()
    root.protocol("WM_DELETE_WINDOW", lambda: (release_all(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
