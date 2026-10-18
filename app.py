from __future__ import annotations

import datetime
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from PIL import Image, ImageEnhance, ImageSequence, ImageTk


APP_TITLE = "Yunzii Screen"
COLORS = {
    "background": "#17191F",
    "surface": "#22252D",
    "preview": "#1D2027",
    "input": "#2B2F39",
    "border": "#3A3F4B",
    "foreground": "#ECEEF4",
    "secondary": "#A7ADBA",
    "disabled": "#737987",
    "accent_active": "#db759e",
    "selection": "#79294C",
}
VENDOR_IDS = {0x28E9, 0x379A}
SCREEN_SIZES = {
    12463: (96, 160),
    2336: (128, 128),
    12545: (160, 96),
    12690: (128, 128),
    12714: (160, 96),
    12724: (240, 135),
    12732: (128, 128),
    12689: (160, 80),
    12744: (160, 96),
    12749: (128, 128),
    12751: (240, 135),
}
REPORT_ID_0_PIDS = {12545, 12463, 12690, 12724, 12732, 12744}
REPORT_LENGTH = 63
CHUNK_LENGTH = REPORT_LENGTH - 7
OP_INFO = 0x40
OP_DATA = 0x41
OP_FINISH = 0x42
ANIMATION_FRAME_LIMIT = 160
ANIMATION_DESTINATIONS = {
    "GIF page": (1, 160),
    "Power-on animation": (0, 64),
}


@dataclass(frozen=True)
class DeviceInfo:
    path: bytes | str
    vendor_id: int
    product_id: int
    product_name: str

    @property
    def label(self) -> str:
        width, height = SCREEN_SIZES.get(self.product_id, (240, 135))
        return (
            f"{self.product_name}  (PID {self.product_id}, "
            f"{width} x {height}{' default' if self.product_id not in SCREEN_SIZES else ''})"
        )


def crc16_modbus(data: bytes) -> bytes:
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xA001 if value & 1 else value >> 1
    return bytes(((value >> 8) & 0xFF, value & 0xFF))


def inner_packet(command: int, length: int, payload: bytes = b"") -> bytes:
    if not 0 <= command <= 0xFF or not 0 <= length <= 0xFFFF:
        raise ValueError("Command or packet length is out of range.")
    header = bytes((command, (length >> 8) & 0xFF, length & 0xFF))
    return b"\xA5\x5A" + header + crc16_modbus(header) + payload


def build_report(op: int, offset: int, chunk: bytes, length_byte: int) -> bytes:
    if len(chunk) > CHUNK_LENGTH:
        raise ValueError(f"HID data chunks cannot exceed {CHUNK_LENGTH} bytes.")
    if not 0 <= offset <= 0xFFFF or not 0 <= length_byte <= 0xFF:
        raise ValueError("HID report offset or length is out of range.")
    report = bytearray(REPORT_LENGTH)
    report[0] = op
    report[1] = offset & 0xFF
    report[2] = (offset >> 8) & 0xFF
    report[3] = length_byte
    report[7 : 7 + len(chunk)] = chunk
    checksum = sum(report) & 0xFFFF
    report[4] = checksum & 0xFF
    report[5] = (checksum >> 8) & 0xFF
    return bytes(report)


def render_image(image: Image.Image, size: tuple[int, int], mode: str) -> Image.Image:
    width, height = size
    background = Image.new("RGB", size, (0, 0, 0))
    source = image.convert("RGBA")
    if mode == "fill":
        resized = source.resize((width, height), Image.Resampling.LANCZOS)
        background.paste(resized, (0, 0), resized.getchannel("A"))
    elif mode == "fit":
        scale = min(width / source.width, height / source.height)
        new_size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
        resized = source.resize(new_size, Image.Resampling.LANCZOS)
        background.paste(
            resized,
            ((width - new_size[0]) // 2, (height - new_size[1]) // 2),
            resized.getchannel("A"),
        )
    else:
        raise ValueError(f"Unknown image sizing mode: {mode}")
    return background


def adjust_brightness(image: Image.Image, percent: int) -> Image.Image:
    if not 0 <= percent <= 200:
        raise ValueError("Image brightness must be between 0% and 200%.")
    return ImageEnhance.Brightness(image).enhance(percent / 100)


def to_rgb565(image: Image.Image) -> bytes:
    rgb = image.convert("RGB")
    pixels = rgb.tobytes()
    output = bytearray(rgb.width * rgb.height * 2)
    for index in range(rgb.width * rgb.height):
        red, green, blue = pixels[index * 3 : index * 3 + 3]
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        output[index * 2] = value >> 8
        output[index * 2 + 1] = value & 0xFF
    return bytes(output)


def gif_frame_count(image: Image.Image) -> int:
    return int(getattr(image, "n_frames", 1))


def gif_default_fps(image: Image.Image) -> int:
    durations = []
    for frame in ImageSequence.Iterator(image):
        duration = int(frame.info.get("duration", image.info.get("duration", 100)))
        if duration > 0:
            durations.append(duration)
    if not durations:
        return 10
    average_duration = sum(durations) / len(durations)
    return max(1, min(60, round(1000 / average_duration)))


def prepare_gif_frames(
    image: Image.Image,
    size: tuple[int, int],
    mode: str,
    max_frames: int = ANIMATION_FRAME_LIMIT,
) -> list[bytes]:
    if image.format != "GIF":
        raise ValueError("Choose a GIF file to upload an animation.")
    count = gif_frame_count(image)
    if count > max_frames:
        raise ValueError(
            f"This GIF has {count} frames; the keyboard supports at most "
            f"{max_frames} for this animation destination."
        )
    frames = [
        to_rgb565(render_image(frame.copy(), size, mode))
        for frame in ImageSequence.Iterator(image)
    ]
    if not frames:
        raise ValueError("The selected GIF contains no animation frames.")
    return frames


def list_devices() -> list[DeviceInfo]:
    try:
        import hid
    except ImportError as exc:
        raise RuntimeError(
            "The HID library is not installed. Run install.bat, then restart the app."
        ) from exc

    devices = []
    for entry in hid.enumerate():
        if entry.get("vendor_id") not in VENDOR_IDS:
            continue
        if entry.get("usage_page") != 0xFF60 or entry.get("usage") != 0x61:
            continue
        product_name = entry.get("product_string") or "Yunzii keyboard display"
        devices.append(
            DeviceInfo(
                path=entry["path"],
                vendor_id=entry["vendor_id"],
                product_id=entry["product_id"],
                product_name=product_name,
            )
        )
    return devices


class KeyboardScreen:
    def __init__(self, device_info: DeviceInfo, report_id: int):
        try:
            import hid
        except ImportError as exc:
            raise RuntimeError(
                "The HID library is not installed. Run install.bat, then restart the app."
            ) from exc
        self.device = hid.device()
        self.device.open_path(device_info.path)
        self.report_id = report_id

    def close(self) -> None:
        self.device.close()

    def __enter__(self) -> KeyboardScreen:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _transfer(self, op: int, offset: int, chunk: bytes, length_byte: int) -> None:
        body = build_report(op, offset, chunk, length_byte)
        report = bytes((self.report_id,)) + body
        for attempt in range(20):
            written = self.device.write(report)
            if written >= 0:
                break
            if attempt < 19:
                time.sleep(0.25)
        else:
            raise OSError(f"HID write failed: {self.device.error()}")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            response = self.device.read(REPORT_LENGTH + 1, timeout_ms=200)
            if response and response[0] == body[0] and response[1] == body[1]:
                return
        raise TimeoutError(f"No reply from keyboard to operation 0x{op:02x}, offset {offset}.")

    def send(self, op: int, data: bytes) -> None:
        chunk_count = max(1, (len(data) + CHUNK_LENGTH - 1) // CHUNK_LENGTH)
        for index in range(chunk_count):
            offset = index * CHUNK_LENGTH
            chunk = data[offset : offset + CHUNK_LENGTH]
            length = len(data) - offset if index == chunk_count - 1 else CHUNK_LENGTH
            self._transfer(op, offset, chunk, length)

    def finish(self) -> None:
        self._transfer(OP_FINISH, 0, b"", CHUNK_LENGTH)

    def switch_page(self, page: str) -> None:
        command = {"home": 11, "picture": 13, "gif": 15}[page]
        self.send(OP_INFO, inner_packet(command, 0))
        self.finish()

    def sync_time(self, now: datetime.datetime | None = None) -> None:
        local_time = now or datetime.datetime.now().astimezone()
        time_bytes = bytes((local_time.hour, local_time.minute, local_time.second))
        date_bytes = bytes(
            (local_time.year % 100, local_time.isoweekday(), local_time.month, local_time.day)
        )
        for _ in range(3):
            self.send(OP_INFO, inner_packet(9, len(time_bytes)))
            self.send(OP_DATA, time_bytes)
            self.finish()
            self.send(OP_INFO, inner_packet(10, len(date_bytes)))
            self.send(OP_DATA, date_bytes)
            self.finish()

    def clear_picture(self, clear_all: bool = False) -> None:
        for _ in range(16 if clear_all else 1):
            self.send(OP_INFO, inner_packet(14, 0))
            self.finish()

    def clear_animation(self, gif_type: int = 1) -> None:
        if gif_type not in (0, 1):
            raise ValueError("Unknown GIF animation destination.")
        animation = bytes((gif_type, 0))
        self.send(OP_INFO, inner_packet(18, 2, animation))
        self.send(OP_DATA, inner_packet(19, 2, animation))
        self.finish()

    def upload_picture(self, pixels: bytes) -> None:
        self.send(OP_INFO, inner_packet(16, 1, b"\x01"))
        time.sleep(0.3)
        self.send(OP_DATA, inner_packet(12, len(pixels)))
        self.send(OP_DATA, pixels)
        self.finish()

    def upload_animation(
        self,
        frames: list[bytes],
        gif_type: int,
        fps: int,
        progress: Callable[[str], None],
    ) -> None:
        if gif_type not in (0, 1):
            raise ValueError("Unknown GIF animation destination.")
        if not frames:
            raise ValueError("An animation must contain at least one frame.")
        frame_limit = ANIMATION_DESTINATIONS[
            "Power-on animation" if gif_type == 0 else "GIF page"
        ][1]
        if len(frames) > frame_limit:
            raise ValueError(
                f"This animation has {len(frames)} frames; the selected destination supports "
                f"at most {frame_limit}."
            )
        frame_length = len(frames[0])
        animation = bytes((gif_type, 0))
        self.send(OP_INFO, inner_packet(18, 2, animation))
        self.send(OP_DATA, inner_packet(19, 2, animation))
        time.sleep(0.03)
        frame_header = inner_packet(17, frame_length)
        for index, frame in enumerate(frames):
            progress(f"Uploading GIF frame {index + 1} of {len(frames)}…")
            self.send(OP_DATA, inner_packet(16, 3, bytes((2, gif_type, index))))
            time.sleep(3.0 if index % 16 == 0 else 0.03)
            self.send(OP_DATA, frame_header)
            for offset in range(0, len(frame), 1024):
                self.send(OP_DATA, frame[offset : offset + 1024])
                time.sleep(0.03)
        self.send(OP_DATA, inner_packet(18, 2, bytes((gif_type, len(frames)))))
        time.sleep(0.03)
        self.send(OP_DATA, inner_packet(19, 2, bytes((gif_type, fps))))
        time.sleep(0.03)
        self.finish()


class YunziScreen:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(640, 600)
        self.root.geometry("760x700")
        self.devices: list[DeviceInfo] = []
        self.selected_path: Path | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.preview_source: Image.Image | None = None
        self.selected_is_gif = False
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False
        self.action_buttons: list[ttk.Button] = []

        self.device_var = tk.StringVar()
        self.width_var = tk.StringVar(value="240")
        self.height_var = tk.StringVar(value="135")
        self.report_id_var = tk.StringVar(value="6")
        self.mode_var = tk.StringVar(value="fit")
        self.animation_destination_var = tk.StringVar(value="GIF page")
        self.fps_var = tk.StringVar(value="10")
        self.brightness_var = tk.IntVar(value=100)
        self.brightness_label_var = tk.StringVar(value="100%")
        self.replace_var = tk.BooleanVar(value=False)
        self.file_var = tk.StringVar(value="No image selected")
        self.status_var = tk.StringVar(value="Connect your keyboard, then refresh devices.")

        self._apply_dark_theme()
        self._build_ui()
        self.root.after(100, self._poll_messages)
        self.root.after(200, self.refresh_devices)

    def _apply_dark_theme(self) -> None:
        self.root.configure(background=COLORS["background"])
        self.root.tk.call("ttk::style", "theme", "use", "clam")
        style = ttk.Style(self.root)
        style.configure(".", background=COLORS["background"], foreground=COLORS["foreground"])
        style.configure("TFrame", background=COLORS["background"])
        style.configure("TLabelframe", background=COLORS["background"], bordercolor=COLORS["border"])
        style.configure(
            "TLabelframe.Label",
            background=COLORS["background"],
            foreground=COLORS["foreground"],
        )
        style.configure("TLabel", background=COLORS["background"], foreground=COLORS["foreground"])
        style.configure(
            "Secondary.TLabel",
            background=COLORS["background"],
            foreground=COLORS["secondary"],
        )
        style.configure(
            "Preview.TLabel",
            background=COLORS["preview"],
            foreground=COLORS["secondary"],
        )
        style.configure(
            "TButton",
            background=COLORS["surface"],
            foreground=COLORS["foreground"],
            bordercolor=COLORS["border"],
            focusthickness=1,
            focuscolor=COLORS["accent_active"],
            padding=(9, 5),
        )
        style.map(
            "TButton",
            background=[
                ("disabled", COLORS["surface"]),
                ("pressed", COLORS["selection"]),
                ("active", COLORS["accent_active"]),
            ],
            foreground=[
                ("disabled", COLORS["disabled"]),
                ("active", "#FFFFFF"),
            ],
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["surface"],
            foreground=COLORS["foreground"],
            bordercolor=COLORS["border"],
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[
                ("disabled", COLORS["surface"]),
                ("pressed", COLORS["selection"]),
                ("active", COLORS["accent_active"]),
            ],
            foreground=[
                ("disabled", COLORS["disabled"]),
                ("active", "#FFFFFF"),
            ],
        )
        for widget_style in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(
                widget_style,
                fieldbackground=COLORS["input"],
                background=COLORS["input"],
                foreground=COLORS["foreground"],
                insertcolor=COLORS["foreground"],
                bordercolor=COLORS["border"],
                arrowcolor=COLORS["foreground"],
                selectbackground=COLORS["selection"],
                selectforeground=COLORS["foreground"],
            )
        style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", COLORS["input"]),
                ("disabled", COLORS["surface"]),
            ],
            foreground=[("disabled", COLORS["disabled"])],
        )
        for widget_style in ("TRadiobutton", "TCheckbutton"):
            style.configure(
                widget_style,
                background=COLORS["background"],
                foreground=COLORS["foreground"],
                indicatorcolor=COLORS["input"],
            )
            style.map(
                widget_style,
                background=[("active", COLORS["background"])],
                foreground=[
                    ("disabled", COLORS["disabled"]),
                    ("selected", COLORS["accent_active"]),
                ],
                indicatorcolor=[
                    ("selected", COLORS["accent_active"]),
                    ("active", COLORS["border"]),
                ],
            )
        style.configure(
            "Horizontal.TProgressbar",
            background=COLORS["accent_active"],
            troughcolor=COLORS["surface"],
            bordercolor=COLORS["border"],
        )
        for option, value in (
            ("*TCombobox*Listbox*Background", COLORS["input"]),
            ("*TCombobox*Listbox*Foreground", COLORS["foreground"]),
            ("*TCombobox*Listbox*selectBackground", COLORS["selection"]),
            ("*TCombobox*Listbox*selectForeground", COLORS["foreground"]),
        ):
            self.root.option_add(option, value)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text="Yunzii Screen", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            outer,
            text="Tailored for the YUNZII B75 PRO MAX built-in screen.",
        ).grid(row=1, column=0, sticky="w", pady=(2, 8))

        device_box = ttk.LabelFrame(outer, text="Keyboard", padding=8)
        device_box.grid(row=2, column=0, sticky="ew")
        device_box.columnconfigure(0, weight=1)
        self.device_combo = ttk.Combobox(device_box, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self._device_selected)
        refresh_button = ttk.Button(device_box, text="Refresh", command=self.refresh_devices)
        refresh_button.grid(row=0, column=1)
        ttk.Label(device_box, text="Screen size").grid(row=1, column=0, sticky="w", pady=(12, 0))
        size_row = ttk.Frame(device_box)
        size_row.grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(size_row, textvariable=self.width_var, width=7).pack(side="left")
        ttk.Label(size_row, text=" × ").pack(side="left")
        ttk.Entry(size_row, textvariable=self.height_var, width=7).pack(side="left")
        ttk.Label(size_row, text="pixels    HID report ID").pack(side="left", padx=(12, 6))
        ttk.Combobox(
            size_row, textvariable=self.report_id_var, values=("0", "6"), state="readonly", width=5
        ).pack(side="left")
        ttk.Label(
            device_box,
            text="Unknown models default to 240 × 135; adjust the size if needed.",
            style="Secondary.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(5, 0))

        file_box = ttk.LabelFrame(outer, text="Image or GIF", padding=8)
        file_box.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        file_box.columnconfigure(0, weight=1)
        ttk.Label(file_box, textvariable=self.file_var).grid(row=0, column=0, sticky="w")
        choose_button = ttk.Button(file_box, text="Choose file…", command=self.choose_file)
        choose_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(file_box, text="Sizing").grid(row=1, column=0, sticky="w", pady=(10, 0))
        options = ttk.Frame(file_box)
        options.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Radiobutton(options, text="Fit (keep proportions)", variable=self.mode_var, value="fit").pack(
            side="left"
        )
        ttk.Radiobutton(options, text="Fill (stretch)", variable=self.mode_var, value="fill").pack(
            side="left", padx=(16, 0)
        )
        ttk.Label(options, text="GIF destination").pack(side="left", padx=(24, 6))
        self.animation_destination_combo = ttk.Combobox(
            options,
            textvariable=self.animation_destination_var,
            values=tuple(ANIMATION_DESTINATIONS),
            state="readonly",
            width=21,
        )
        self.animation_destination_combo.pack(side="left")
        ttk.Label(options, text="FPS").pack(side="left", padx=(12, 6))
        ttk.Spinbox(options, textvariable=self.fps_var, from_=1, to=60, width=5).pack(side="left")
        ttk.Checkbutton(
            file_box,
            text="Replace existing picture(s) before uploading (clears all 16 slots)",
            variable=self.replace_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            file_box,
            text="Each GIF destination stores one animation; a new upload replaces its previous GIF.",
            style="Secondary.TLabel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(5, 0))
        brightness_row = ttk.Frame(file_box)
        brightness_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Label(brightness_row, text="Image brightness").pack(side="left")
        self.brightness_scale = ttk.Scale(
            brightness_row,
            from_=0,
            to=200,
            orient="horizontal",
            variable=self.brightness_var,
            command=self._brightness_changed,
            length=220,
        )
        self.brightness_scale.pack(side="left", padx=(8, 4))
        ttk.Label(brightness_row, textvariable=self.brightness_label_var, width=5).pack(side="left")
        ttk.Label(
            brightness_row,
            text="Still images only; 100% is unchanged.",
            style="Secondary.TLabel",
        ).pack(side="left", padx=(8, 0))

        preview_box = ttk.LabelFrame(outer, text="Preview", padding=4, width=600, height=108)
        preview_box.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        preview_box.grid_propagate(False)
        preview_box.columnconfigure(0, weight=1)
        preview_box.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(
            preview_box,
            text="Choose an image or GIF to preview it here.",
            anchor="center",
            justify="center",
            style="Preview.TLabel",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")

        actions = ttk.Frame(outer)
        actions.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        action_specs = (
            ("Upload image", lambda: self._upload("image"), "Accent.TButton"),
            ("Upload GIF", lambda: self._upload("gif"), "Accent.TButton"),
            ("Clear images", self._clear_pictures, "TButton"),
            ("Clear GIF", self._clear_gif, "TButton"),
            ("Clear startup image/GIF", self._clear_startup_gif, "TButton"),
        )
        for index, (label, callback, button_style) in enumerate(action_specs):
            button = ttk.Button(actions, text=label, command=callback, style=button_style)
            button.grid(row=0, column=index, padx=(0, 8), sticky="ew")
            self.action_buttons.append(button)
            actions.columnconfigure(index, weight=1)

        pages = ttk.Frame(outer)
        pages.grid(row=6, column=0, sticky="w", pady=(6, 0))
        for label, page in (("Show home page", "home"), ("Show picture page", "picture"), ("Show GIF page", "gif")):
            button = ttk.Button(pages, text=label, command=lambda selected=page: self._switch_page(selected))
            button.pack(side="left", padx=(0, 8))
            self.action_buttons.append(button)
        time_button = ttk.Button(
            pages,
            text="Sync time from PC",
            command=self._sync_time,
            style="Accent.TButton",
        )
        time_button.pack(side="left")
        self.action_buttons.append(time_button)

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(outer, textvariable=self.status_var, wraplength=690).grid(
            row=8, column=0, sticky="w", pady=(4, 0)
        )

    def refresh_devices(self) -> None:
        try:
            self.devices = list_devices()
        except RuntimeError as exc:
            self.status_var.set(str(exc))
            messagebox.showerror("Missing dependency", str(exc), parent=self.root)
            return
        except OSError as exc:
            self.status_var.set(f"Could not scan HID devices: {exc}")
            messagebox.showerror("Device scan failed", str(exc), parent=self.root)
            return

        self.device_combo["values"] = [device.label for device in self.devices]
        if self.devices:
            self.device_combo.current(0)
            self._device_selected()
            self.status_var.set(f"Found {len(self.devices)} Yunzii display device(s).")
        else:
            self.device_var.set("")
            self.status_var.set("No compatible Yunzii display found. Check the USB connection and refresh.")

    def _device_selected(self, _event: object = None) -> None:
        index = self.device_combo.current()
        if not 0 <= index < len(self.devices):
            return
        device = self.devices[index]
        width, height = SCREEN_SIZES.get(device.product_id, (240, 135))
        self.width_var.set(str(width))
        self.height_var.set(str(height))
        self.report_id_var.set("0" if device.product_id in REPORT_ID_0_PIDS else "6")

    def choose_file(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root,
            title="Choose an image or GIF",
            filetypes=(
                ("Images and GIFs", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                ("All files", "*.*"),
            ),
        )
        if not filename:
            return
        self.selected_path = Path(filename)
        self.file_var.set(self.selected_path.name)
        try:
            with Image.open(self.selected_path) as image:
                self.selected_is_gif = image.format == "GIF"
                fps = gif_default_fps(image) if self.selected_is_gif else None
                image.seek(0)
                self.preview_source = image.convert("RGB")
                self.preview_source.thumbnail((560, 70), Image.Resampling.LANCZOS)
            self.brightness_var.set(100)
            self.brightness_scale.configure(state="disabled" if self.selected_is_gif else "normal")
            self._update_preview()
            if fps is not None:
                self.fps_var.set(str(fps))
            self.status_var.set(f"Ready: {self.selected_path.name}")
        except (OSError, ValueError) as exc:
            self.preview_photo = None
            self.preview_source = None
            self.preview_label.configure(image="", text="This file could not be previewed.")
            messagebox.showerror("Invalid image", str(exc), parent=self.root)

    def _brightness_changed(self, value: str) -> None:
        brightness = round(float(value))
        self.brightness_var.set(brightness)
        self.brightness_label_var.set(f"{brightness}%")
        self._update_preview()

    def _update_preview(self) -> None:
        if self.preview_source is None:
            return
        preview = self.preview_source
        if not self.selected_is_gif:
            preview = adjust_brightness(preview, self.brightness_var.get())
        self.preview_photo = ImageTk.PhotoImage(preview, master=self.root)
        self.preview_label.configure(image=self.preview_photo, text="")

    def _selection(self) -> tuple[DeviceInfo, tuple[int, int], int]:
        index = self.device_combo.current()
        if not 0 <= index < len(self.devices):
            raise ValueError("Connect a compatible Yunzii keyboard and select its display first.")
        try:
            width = int(self.width_var.get())
            height = int(self.height_var.get())
            report_id = int(self.report_id_var.get())
        except ValueError as exc:
            raise ValueError("Enter valid screen dimensions and a report ID of 0 or 6.") from exc
        if not 1 <= width <= 1024 or not 1 <= height <= 1024:
            raise ValueError("Screen dimensions must be between 1 and 1024 pixels.")
        if width * height > 0xFFFF // 2:
            raise ValueError("The screen size exceeds the keyboard's RGB565 frame limit.")
        if report_id not in (0, 6):
            raise ValueError("The HID report ID must be 0 or 6.")
        return self.devices[index], (width, height), report_id

    def _start_operation(self, description: str, operation: Callable[[], None]) -> None:
        if self.busy:
            return
        self.busy = True
        self.progress.start(12)
        self.status_var.set(description)
        for button in self.action_buttons:
            button.configure(state="disabled")

        def worker() -> None:
            try:
                operation()
            except Exception as exc:
                self.messages.put(("error", str(exc)))
            else:
                self.messages.put(("done", "Operation completed successfully."))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, text = self.messages.get_nowait()
                if kind == "progress":
                    self.status_var.set(text)
                else:
                    self.busy = False
                    self.progress.stop()
                    for button in self.action_buttons:
                        button.configure(state="normal")
                    self.status_var.set(text)
                    if kind == "error":
                        messagebox.showerror("Keyboard operation failed", text, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)

    def _upload(self, kind: str) -> None:
        if self.selected_path is None:
            messagebox.showwarning("Choose a file", "Choose an image or GIF first.", parent=self.root)
            return
        try:
            device, size, report_id = self._selection()
            fps = int(self.fps_var.get())
            if not 1 <= fps <= 60:
                raise ValueError("GIF playback FPS must be between 1 and 60.")
            if self.animation_destination_var.get() not in ANIMATION_DESTINATIONS:
                raise ValueError("Choose a valid GIF destination.")
        except ValueError as exc:
            messagebox.showwarning("Check settings", str(exc), parent=self.root)
            return

        path = self.selected_path
        mode = self.mode_var.get()
        replace = self.replace_var.get()
        gif_type, frame_limit = ANIMATION_DESTINATIONS[self.animation_destination_var.get()]

        def operation() -> None:
            with Image.open(path) as source:
                if kind == "gif":
                    frames = prepare_gif_frames(source, size, mode, max_frames=frame_limit)
                    with KeyboardScreen(device, report_id) as screen:
                        screen.upload_animation(
                            frames,
                            gif_type,
                            fps,
                            lambda text: self.messages.put(("progress", text)),
                        )
                else:
                    image = render_image(source.copy(), size, mode)
                    image = adjust_brightness(image, self.brightness_var.get())
                    pixels = to_rgb565(image)
                    with KeyboardScreen(device, report_id) as screen:
                        if replace:
                            screen.clear_picture(clear_all=True)
                        screen.upload_picture(pixels)

        self._start_operation(f"Uploading {kind}…", operation)

    def _clear_pictures(self) -> None:
        try:
            device, _size, report_id = self._selection()
        except ValueError as exc:
            messagebox.showwarning("Select a keyboard", str(exc), parent=self.root)
            return
        if not messagebox.askyesno(
            "Clear all pictures?",
            "This removes all 16 stored picture slots from the keyboard.",
            parent=self.root,
        ):
            return
        self._start_operation(
            "Clearing picture slots…",
            lambda: self._run_device_operation(
                device, report_id, lambda screen: screen.clear_picture(clear_all=True)
            ),
        )

    def _clear_gif(self) -> None:
        self._clear_animation("GIF page")

    def _clear_startup_gif(self) -> None:
        self._clear_animation("Power-on animation")

    def _clear_animation(self, destination: str) -> None:
        try:
            device, _size, report_id = self._selection()
        except ValueError as exc:
            messagebox.showwarning("Select a keyboard", str(exc), parent=self.root)
            return
        gif_type, _frame_limit = ANIMATION_DESTINATIONS[destination]
        if not messagebox.askyesno(
            f"Clear {destination.lower()}?",
            (
                "This clears the boot-animation slot, including a still or single-frame "
                "startup image."
                if destination == "Power-on animation"
                else f"This clears the animation stored as the {destination.lower()}."
            ),
            parent=self.root,
        ):
            return
        self._start_operation(
            f"Clearing {destination.lower()}…",
            lambda: self._run_device_operation(
                device, report_id, lambda screen: screen.clear_animation(gif_type)
            ),
        )

    def _switch_page(self, page: str) -> None:
        try:
            device, _size, report_id = self._selection()
        except ValueError as exc:
            messagebox.showwarning("Select a keyboard", str(exc), parent=self.root)
            return
        self._start_operation(
            f"Switching to the {page} page…",
            lambda: self._run_device_operation(
                device, report_id, lambda screen: screen.switch_page(page)
            ),
        )

    def _sync_time(self) -> None:
        try:
            device, _size, report_id = self._selection()
        except ValueError as exc:
            messagebox.showwarning("Select a keyboard", str(exc), parent=self.root)
            return
        self._start_operation(
            "Syncing keyboard date and time from the PC's local clock…",
            lambda: self._run_device_operation(device, report_id, lambda screen: screen.sync_time()),
        )

    @staticmethod
    def _run_device_operation(
        device: DeviceInfo, report_id: int, operation: Callable[[KeyboardScreen], None]
    ) -> None:
        with KeyboardScreen(device, report_id) as screen:
            operation(screen)


def main() -> None:
    root = tk.Tk()
    YunziScreen(root)
    root.mainloop()


if __name__ == "__main__":
    main()
