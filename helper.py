import json
import socket
import struct
import threading
import time
import os
import platform

import cv2
import mss
import numpy as np

from Xlib import X, XK, display
from Xlib.ext import xtest

if platform.system() != "Linux":
    raise RuntimeError("This script is for Linux.")

HOST = os.environ.get("CONTROL_HOST", "127.0.0.1")
PORT = int(os.environ.get("CONTROL_PORT", "9000"))
FPS = int(os.environ.get("CONTROL_FPS", "15"))
JPEG_QUALITY = int(os.environ.get("CONTROL_JPEG_QUALITY", "45"))
SCALE = float(os.environ.get("CONTROL_SCALE", "0.75"))
MOUSE_FPS = int(os.environ.get("CONTROL_MOUSE_FPS", "60"))
MONITOR_INDEX = int(os.environ.get("CONTROL_MONITOR", "1"))
DISPLAY_NAME = os.environ.get("DISPLAY", ":0")
XAUTHORITY = os.environ.get("XAUTHORITY", os.path.expanduser("~/.Xauthority"))

print(f"Using X display {DISPLAY_NAME}")
dpy = display.Display(DISPLAY_NAME)
root = dpy.screen().root

sock = socket.create_connection((HOST, PORT))
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
print(f"Connected to {HOST}:{PORT}")

sct = mss.mss()
monitor = sct.monitors[MONITOR_INDEX]
SCREEN_WIDTH = monitor["width"]
SCREEN_HEIGHT = monitor["height"]
print(
    f"Capturing monitor {MONITOR_INDEX}: "
    f"{SCREEN_WIDTH}x{SCREEN_HEIGHT} at ({monitor['left']}, {monitor['top']})"
)

KEYMAP = {
    **{f"Key{c}": XK.string_to_keysym(c.lower()) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{f"Digit{i}": XK.string_to_keysym(str(i)) for i in range(10)},
    "Enter": XK.XK_Return,
    "Escape": XK.XK_Escape,
    "Backspace": XK.XK_BackSpace,
    "Tab": XK.XK_Tab,
    "Space": XK.XK_space,
    "Insert": XK.XK_Insert,
    "Delete": XK.XK_Delete,
    "Home": XK.XK_Home,
    "End": XK.XK_End,
    "PageUp": XK.XK_Page_Up,
    "PageDown": XK.XK_Page_Down,
    "ArrowUp": XK.XK_Up,
    "ArrowDown": XK.XK_Down,
    "ArrowLeft": XK.XK_Left,
    "ArrowRight": XK.XK_Right,
    "ShiftLeft": XK.XK_Shift_L,
    "ShiftRight": XK.XK_Shift_R,
    "ControlLeft": XK.XK_Control_L,
    "ControlRight": XK.XK_Control_R,
    "AltLeft": XK.XK_Alt_L,
    "AltRight": XK.XK_Alt_R,
    "MetaLeft": XK.XK_Super_L,
    "MetaRight": XK.XK_Super_R,
    "CapsLock": XK.XK_Caps_Lock,
    "NumLock": XK.XK_Num_Lock,
    "ScrollLock": XK.XK_Scroll_Lock,
    "Minus": XK.XK_minus,
    "Equal": XK.XK_equal,
    "BracketLeft": XK.XK_bracketleft,
    "BracketRight": XK.XK_bracketright,
    "Backslash": XK.XK_backslash,
    "Semicolon": XK.XK_semicolon,
    "Quote": XK.XK_apostrophe,
    "Comma": XK.XK_comma,
    "Period": XK.XK_period,
    "Slash": XK.XK_slash,
    "Backquote": XK.XK_grave,
}

held_keys = set()
held_buttons = set()
mouse_lock = threading.Lock()
mouse_event = threading.Event()
target_x = 0
target_y = 0
mouse_moved = False


def send_key(keysym, is_down):
    keycode = dpy.keysym_to_keycode(keysym)
    if keycode == 0:
        return
    xtest.fake_input(dpy, X.KeyPress if is_down else X.KeyRelease, keycode)
    dpy.sync()


def send_mouse_move(x, y):
    root.warp_pointer(x, y)
    dpy.sync()


def send_mouse_button(button, is_down, x, y):
    send_mouse_move(x, y)
    mapping = {0: 1, 1: 2, 2: 3}
    btn = mapping.get(button, 3)
    xtest.fake_input(dpy, X.ButtonPress if is_down else X.ButtonRelease, btn)
    dpy.sync()
    if is_down:
        held_buttons.add(button)
    else:
        held_buttons.discard(button)


def send_mouse_wheel(dx, dy):
    if dy:
        btn = 4 if dy > 0 else 5
        for _ in range(abs(int(dy))):
            xtest.fake_input(dpy, X.ButtonPress, btn)
            xtest.fake_input(dpy, X.ButtonRelease, btn)
        dpy.sync()

    if dx:
        btn = 6 if dx > 0 else 7
        for _ in range(abs(int(dx))):
            xtest.fake_input(dpy, X.ButtonPress, btn)
            xtest.fake_input(dpy, X.ButtonRelease, btn)
        dpy.sync()


def release_all_inputs():
    for vk in list(held_keys):
        send_key(vk, False)
    held_keys.clear()

    for button in list(held_buttons):
        send_mouse_button(button, False, target_x, target_y)
    held_buttons.clear()


def resolve_key(event):
    code = event.get("code")
    sym = KEYMAP.get(code)
    if sym is not None:
        return sym

    text = event.get("key")
    if isinstance(text, str) and len(text) == 1:
        sym = XK.string_to_keysym(text)
        if sym != 0:
            return sym

    return None


def recv_exact(conn, size):
    data = bytearray(size)
    view = memoryview(data)
    received = 0

    while received < size:
        packet_size = conn.recv_into(view[received:], size - received)
        if packet_size == 0:
            raise ConnectionError("socket closed")
        received += packet_size

    return data


def handle_immediate_input(event):
    ev_type = event.get("type")
    x = int(event.get("x", 0) * SCREEN_WIDTH) + monitor["left"]
    y = int(event.get("y", 0) * SCREEN_HEIGHT) + monitor["top"]

    if ev_type == "mousedown":
        button = int(event.get("button", 2))
        send_mouse_button(button, True, x, y)

    elif ev_type == "mouseup":
        button = int(event.get("button", 2))
        send_mouse_button(button, False, x, y)

    elif ev_type == "wheel":
        send_mouse_wheel(event.get("dx", 0), event.get("dy", 0))

    elif ev_type in ("keydown", "keyup"):
        sym = resolve_key(event)
        if sym is None:
            print("Unknown key:", event.get("code"), event.get("key"))
            return
        is_down = ev_type == "keydown"
        send_key(sym, is_down)
        if is_down:
            held_keys.add(sym)
        else:
            held_keys.discard(sym)

    elif ev_type == "release_all":
        release_all_inputs()


def input_receiver():
    global target_x, target_y, mouse_moved

    while True:
        try:
            header = recv_exact(sock, 4)
            size = struct.unpack("!I", header)[0]
            data = recv_exact(sock, size)

            event = json.loads(data.decode("utf-8"))
            ev_type = event.get("type")

            if ev_type == "mousemove":
                x = int(event.get("x", 0) * SCREEN_WIDTH) + monitor["left"]
                y = int(event.get("y", 0) * SCREEN_HEIGHT) + monitor["top"]

                with mouse_lock:
                    target_x = x
                    target_y = y
                    mouse_moved = True
                    mouse_event.set()
            else:
                handle_immediate_input(event)

        except Exception as e:
            print("Input receiver error:", e)
            break


def mouse_executor():
    global mouse_moved, target_x, target_y

    while True:
        mouse_event.wait()
        if mouse_moved:
            with mouse_lock:
                x, y = target_x, target_y
                mouse_moved = False
                mouse_event.clear()
            send_mouse_move(x, y)
        time.sleep(1 / MOUSE_FPS)


threading.Thread(target=input_receiver, daemon=True).start()
threading.Thread(target=mouse_executor, daemon=True).start()

frame_delay = 1 / FPS
jpeg_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
resize_to = None
if SCALE != 1.0:
    resize_to = (max(1, int(SCREEN_WIDTH * SCALE)), max(1, int(SCREEN_HEIGHT * SCALE)))

sent_frames = 0

while True:
    try:
        img = np.array(sct.grab(monitor))
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        if resize_to:
            img = cv2.resize(img, resize_to, interpolation=cv2.INTER_AREA)

        _, jpg = cv2.imencode(".jpg", img, jpeg_params)
        data = jpg.tobytes()

        sock.sendall(struct.pack("!I", len(data)) + data)
        sent_frames += 1
        if sent_frames == 1:
            print(f"First frame sent: {len(data)} bytes")
        time.sleep(frame_delay)

    except Exception as e:
        print("Screen send error:", e)
        break