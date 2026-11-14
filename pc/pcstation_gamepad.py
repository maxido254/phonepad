"""PCStation Gamepad (PC): receives controller input from up to 4 phones over Wi-Fi and
presents each phone to Windows as its own virtual Xbox 360 gamepad (Player 1-4), so you can
play local multiplayer games with friends. Double-click, then open PCStation Gamepad on each phone.
Works fully offline on any shared network (home Wi-Fi, phone hotspot, PC hotspot).
"""
import json
import socket
import threading
import time
import tkinter as tk

APP_NAME = "PCStation Gamepad"
PORT = 8765
MAX_PLAYERS = 4        # XInput (the Xbox 360 pad API that games use) only exposes 4 controllers
WATCHDOG_SECONDS = 3   # release a phone's inputs if it goes quiet
DROP_SECONDS = 20      # ...and free its player slot after this long (it can rejoin the same slot sooner)
PLAYER_COLORS = ["#2f7bff", "#ff4d5a", "#3fb950", "#f08ad8"]   # same colours the phones show

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

pad_error = IMPORT_ERROR
lock = threading.Lock()

players = {}   # slot (1..MAX_PLAYERS) -> Player
by_addr = {}   # (ip, port) -> Player   (a phone keeps one socket for a whole session)
by_id = {}     # phone's persistent id -> Player (lets a reconnecting phone get its slot back)


class Player:
    def __init__(self, pid, slot, addr, name):
        global pad_error
        self.pid, self.slot, self.addr, self.name = pid, slot, addr, name
        self.last_seen = time.time()
        self.connected = True
        self.pad = None
        if vg is not None:
            try:
                self.pad = vg.VX360Gamepad()      # one virtual Xbox 360 pad per phone
            except Exception as exc:
                pad_error = str(exc)


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


def apply(p, d):
    pad = p.pad
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


def release(p):
    if p.pad is not None:
        p.pad.reset()
        p.pad.update()


def join(pid, addr, name):
    """Find this phone's player, or give it the lowest free slot. Call with lock held."""
    p = by_id.get(pid)
    if p is not None:                      # known phone (maybe reconnecting from a new port)
        by_addr.pop(p.addr, None)
        p.addr = addr
        if name:
            p.name = name
    else:
        slot = next((i for i in range(1, MAX_PLAYERS + 1) if i not in players), None)
        if slot is None:
            return None                    # all slots taken
        p = Player(pid, slot, addr, name)
        players[slot] = p
        by_id[pid] = p
    by_addr[addr] = p
    return p


def remove(p):
    """Unplug a player's virtual pad and free its slot. Call with lock held."""
    release(p)
    players.pop(p.slot, None)
    by_addr.pop(p.addr, None)
    by_id.pop(p.pid, None)
    p.pad = None                           # dropping the pad unplugs the virtual controller


def release_all_players():
    with lock:
        for p in list(players.values()):
            remove(p)


def open_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", PORT))
    if hasattr(socket, "SIO_UDP_CONNRESET"):   # Windows: don't raise when a phone vanishes
        try:
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        except (OSError, ValueError):
            pass
    return sock


def listen(sock):
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            continue
        try:
            handle(sock, data, addr)
        except Exception:                       # never let one bad packet kill the listener
            pass


def handle(sock, data, addr):
    if data.startswith(b"PHONEPAD?"):          # a phone is looking for this PC
        sock.sendto(b"PHONEPAD!", addr)
    elif data.startswith(b"PING"):             # keep-alive: "PING|<phone id>|<phone name>"
        parts = data.decode("utf-8", "ignore").strip().split("|")
        pid = parts[1] if len(parts) > 1 and parts[1] else addr[0]
        name = parts[2][:24] if len(parts) > 2 else ""
        with lock:
            p = join(pid, addr, name)
            if p is not None:
                p.last_seen = time.time()
                p.connected = True
                reply = "PONG|%d" % p.slot     # tells the phone which player it is
            else:
                reply = "FULL"
        sock.sendto(reply.encode(), addr)
    elif data.startswith(b"BYE"):              # phone left the controller screen
        with lock:
            p = by_addr.get(addr)
            if p is not None:
                remove(p)
    else:
        try:
            d = json.loads(data.decode("utf-8"))
        except ValueError:
            return
        with lock:
            p = by_addr.get(addr)
            if p is not None:
                p.last_seen = time.time()
                p.connected = True
                apply(p, d)


def maintain():
    """Release inputs of quiet phones; free the slots of ones that are gone. Call with lock held."""
    now = time.time()
    for p in list(players.values()):
        idle = now - p.last_seen
        if idle > DROP_SECONDS:
            remove(p)
        elif idle > WATCHDOG_SECONDS and p.connected:
            release(p)
            p.connected = False


def main():
    make_error = None
    try:
        sock = open_socket()
        threading.Thread(target=listen, args=(sock,), daemon=True).start()
    except OSError as exc:
        make_error = str(exc)

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("420x400")
    root.resizable(False, False)
    root.configure(bg="#0f1318")

    def label(text, size=11, color="#e6edf3", bold=False, pady=4):
        w = tk.Label(root, text=text, bg="#0f1318", fg=color, wraplength=390, justify="center",
                     font=("Segoe UI", size, "bold" if bold else "normal"))
        w.pack(pady=pady)
        return w

    label(APP_NAME, 18, bold=True)
    label(f"This PC's address: {local_ip()}", 11, "#9fb0c3")
    state = label("", 12, bold=True)

    rows = []
    box = tk.Frame(root, bg="#0f1318")
    box.pack(pady=6, fill="x", padx=30)
    for i in range(MAX_PLAYERS):
        row = tk.Frame(box, bg="#171d25")
        row.pack(fill="x", pady=3, ipady=4)
        dot = tk.Label(row, text="\u25cf", bg="#171d25", fg=PLAYER_COLORS[i % len(PLAYER_COLORS)], font=("Segoe UI", 14))
        dot.pack(side="left", padx=(10, 6))
        who = tk.Label(row, text="", bg="#171d25", fg="#e6edf3", anchor="w", font=("Segoe UI", 11, "bold"))
        who.pack(side="left", fill="x", expand=True)
        st = tk.Label(row, text="", bg="#171d25", anchor="e", font=("Segoe UI", 10))
        st.pack(side="right", padx=10)
        rows.append((who, st))

    note = label("", 9, "#9fb0c3")

    def refresh():
        with lock:
            maintain()
            snap = {s: (p.name, p.addr[0], p.connected) for s, p in players.items()}
        for i, (who, st) in enumerate(rows, start=1):
            if i in snap:
                name, ip, ok = snap[i]
                who.config(text=f"Player {i}   {name + ' ' if name else ''}({ip})", fg="#e6edf3")
                st.config(text="connected" if ok else "reconnecting\u2026", fg="#3fb950" if ok else "#d29922")
            else:
                who.config(text=f"Player {i}", fg="#6b7785")
                st.config(text="waiting\u2026", fg="#6b7785")
        if pad_error:
            state.config(text="Virtual gamepad unavailable", fg="#f85149")
            note.config(text="Install the ViGEmBus driver (github.com/nefarius/ViGEmBus/releases), "
                             "then restart this app.\n" + pad_error[:120])
        elif make_error:
            state.config(text="Could not open port", fg="#f85149")
            note.config(text=make_error)
        else:
            n = len(snap)
            state.config(text=f"{n} of {MAX_PLAYERS} players connected" if n else "Waiting for phones\u2026",
                         fg="#3fb950" if n else "#d29922")
            note.config(text=f"Up to {MAX_PLAYERS} phones can join at once; each one is its own Xbox 360 controller.\n"
                             "Open PCStation Gamepad on each phone (same Wi-Fi) and tap \u201cFind my PC\u201d.\n"
                             "If Windows asks, allow access on private networks.")
        root.after(500, refresh)

    refresh()
    root.protocol("WM_DELETE_WINDOW", lambda: (release_all_players(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
