import os
import sys
import time
import ctypes
import argparse
from ctypes import wintypes

import psutil
import win32con
import win32gui

from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal, QPoint
from PyQt6.QtGui import QFont, QPixmap, QAction
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QHBoxLayout, QVBoxLayout, QMenu
)


# ============================================================
# Resource helper
# ============================================================

def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


    
# ============================================================
# Command-line arguments
# ============================================================

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="netspeed",
        description="Taskbar network speed widget",
    )
    parser.add_argument(
        "-w", "--width", type=int, default=148,
        help="Widget width in pixels (default: 148)",
    )
    return parser.parse_args(argv)


# ============================================================
# Win32 helpers
# ============================================================

user32 = ctypes.windll.user32

# --- WinEvent constants ---
EVENT_OBJECT_CREATE         = 0x8000
EVENT_OBJECT_DESTROY        = 0x8001
EVENT_OBJECT_SHOW           = 0x8002
EVENT_OBJECT_HIDE           = 0x8003
EVENT_OBJECT_LOCATIONCHANGE = 0x800B

WINEVENT_OUTOFCONTEXT   = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

# --- WinEvent callback signature ---
WinEventProc = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,   # hWinEventHook
    wintypes.DWORD,    # event
    wintypes.HWND,     # hwnd
    wintypes.LONG,     # idObject
    wintypes.LONG,     # idChild
    wintypes.DWORD,    # idEventThread
    wintypes.DWORD,    # dwmsEventTime
)

# --- DPI helper signature ---
user32.GetDpiForWindow.restype  = ctypes.c_uint
user32.GetDpiForWindow.argtypes = [wintypes.HWND]

# Design baseline: every *_WIDTH / *_HEIGHT / FONT_SIZE below was tuned
# under a 200% Windows display scale, i.e. DPI = 192.
DESIGN_DPI = 192

def get_dpi_scale():
    """Return the scale factor relative to the 200% design baseline.

    200% -> 1.0
    150% -> 0.75
    100% -> 0.5
    """
    hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
    if hwnd:
        try:
            dpi = user32.GetDpiForWindow(hwnd)
            if dpi:
                return dpi / DESIGN_DPI
        except Exception:
            pass
    return 1.0

def find_window_class(parent_hwnd, class_name):
    """Recursively search for the first child window with the given class name."""
    result = []

    def callback(hwnd, _):
        try:
            if win32gui.GetClassName(hwnd) == class_name:
                result.append(hwnd)
        except Exception:
            pass
        return True

    win32gui.EnumChildWindows(parent_hwnd, callback, None)
    return result[0] if result else None


# ============================================================
# Network speed widget
# ============================================================

class NetSpeedWidget(QWidget):

    # --- Window ---
    WIDTH = 148 
    HEIGHT = 40

    # --- Layout ---
    TEXT_WIDTH = 55
    ICON_WIDTH = 32
    ICON_HEIGHT = 44

    # --- Typography ---
    FONT = "Segoe UI"
    FONT_SIZE = 9

    # Emitted from the Win32 callback. Args: (event, hwnd).
    # Queued dispatch guarantees the slot runs in the Qt thread.
    win_event = pyqtSignal(int, int)

    def __init__(self, args=None):
        super().__init__()

        # Apply CLI overrides
        if args is not None:
            self.WIDTH = args.width

        # Apply DPI scale
        scale = get_dpi_scale()
        self.WIDTH = int(self.WIDTH * scale)
        
        # Taskbar window handles
        self.taskbar = None
        self.taskrebar = None
        self.taskbar_view = None
        self.tray = None

        # Network speed state
        self.last_recv = 0
        self.last_sent = 0
        self.last_time = time.monotonic()

        # Embedding state
        self._embedded = False
        self._in_reposition = False
        self.last_position = None

        # Win32 hook state
        self._win_event_hook = None
        self._win_event_cb = None

    # ========================================================
    # Startup
    # ========================================================

    def start(self):
        self.setup_ui()
        self.setup_network_timer()
        self.setup_win_event_hook()
        self.setup_fallback_timer()

        # Defer embedding until the Qt event loop is running.
        QTimer.singleShot(0, self.embed_to_taskbar)

    # ========================================================
    # UI
    # ========================================================

    def setup_ui(self):
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            False
        )

        self.setStyleSheet("""
            QWidget { background: transparent; }
            QLabel  { color: white; background: transparent; }
        """)

        # --- Speed labels ---
        self.down_label = QLabel("0.0 KB/s")
        self.up_label = QLabel("0.0 KB/s")

        font = QFont(self.FONT, self.FONT_SIZE)
        self.down_label.setFont(font)
        self.up_label.setFont(font)

        self.down_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.down_label.setFixedWidth(self.TEXT_WIDTH)

        self.up_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.up_label.setFixedWidth(self.TEXT_WIDTH)

        # --- Text container (upload on top, download below) ---
        text_box = QWidget()
        v = QVBoxLayout(text_box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.up_label)
        v.addWidget(self.down_label)

        # --- Icon ---
        self.icon_label = QLabel()
        self.icon_label.setStyleSheet("background: transparent;")

        icon_path = resource_path("resource//netspeed@2x.png")
        pix = QPixmap(icon_path)

        if not pix.isNull():
            pix = pix.scaled(
                QSize(self.ICON_WIDTH, self.ICON_HEIGHT),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.icon_label.setPixmap(pix)

        self.icon_label.setFixedSize(self.ICON_WIDTH // 2, self.ICON_HEIGHT // 2)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # --- Main layout ---
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(text_box, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

    # ========================================================
    # Network speed
    # ========================================================

    def setup_network_timer(self):
        self.last_recv, self.last_sent = self.get_bytes()
        self.last_time = time.monotonic()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_speed)
        self.timer.start(1000)

    def get_bytes(self):
        counters = psutil.net_io_counters()
        return counters.bytes_recv, counters.bytes_sent

    @staticmethod
    def format_speed(speed):
        if speed >= 1024 * 1024 * 1024:
            return f"{speed / 1024 / 1024 / 1024:.1f} GB/s"
        if speed >= 1024 * 1024:
            return f"{speed / 1024 / 1024:.1f} MB/s"
        return f"{speed / 1024:.1f} KB/s"

    def update_speed(self):
        recv, sent = self.get_bytes()

        now = time.monotonic()
        dt = now - self.last_time
        if dt <= 0:
            return

        down_speed = (recv - self.last_recv) / dt
        up_speed = (sent - self.last_sent) / dt

        self.last_recv = recv
        self.last_sent = sent
        self.last_time = now

        self.down_label.setText(self.format_speed(max(0, down_speed)))
        self.up_label.setText(self.format_speed(max(0, up_speed)))

    # ========================================================
    # WinEvent hook (event-driven taskbar monitoring)
    # ========================================================

    def setup_win_event_hook(self):
        """Install a global WinEvent hook for taskbar-related changes.

        The callback runs on the Qt main thread (the hook is registered with
        WINEVENT_OUTOFCONTEXT, so events are delivered through the thread's
        message queue, which is pumped by Qt's event loop).
        """
        # Ensure the slot runs on the Qt thread, even if a callback ever
        # arrives from a different thread in the future.
        self.win_event.connect(
            self.on_win_event,
            Qt.ConnectionType.QueuedConnection
        )

        def _cb(hook, event, hwnd, idObject, idChild, tid, ts):
            if hwnd:
                self.win_event.emit(event, int(hwnd))

        # Keep a strong reference; otherwise Python may GC the callback and
        # Windows will call into freed memory.
        self._win_event_cb = WinEventProc(_cb)

        self._win_event_hook = user32.SetWinEventHook(
            EVENT_OBJECT_CREATE,
            EVENT_OBJECT_LOCATIONCHANGE,
            0,
            self._win_event_cb,
            0,  # all processes
            0,  # all threads
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        )

    def on_win_event(self, event, hwnd):
        """Handle a forwarded WinEvent.

        This is intentionally cheap: filter aggressively, then delegate.
        """
        # Fast path: is it one of the HWNDs we care about?
        known_hwnd = hwnd in (self.taskbar, self.taskbar_view, self.tray)

        if not known_hwnd:
            # Unknown HWND: it may be a *new* taskbar/tray after Explorer
            # restarts. Only react to create/destroy; ignore location churn
            # from unrelated windows.
            if event in (EVENT_OBJECT_CREATE, EVENT_OBJECT_DESTROY):
                self._revalidate_taskbar()
            return

        if event == EVENT_OBJECT_LOCATIONCHANGE:
            self.reposition_widget()
        elif event in (EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE):
            self._revalidate_taskbar()

    def _revalidate_taskbar(self):
        """Re-check taskbar HWNDs and re-embed if anything changed."""
        current = win32gui.FindWindow("Shell_TrayWnd", None)

        if not current or current != self.taskbar:
            # Explorer restarted or taskbar disappeared.
            self._embedded = False
            self.taskbar = None
            self.taskbar_view = None
            self.tray = None
            self.embed_to_taskbar()
            return

    # ========================================================
    # Fallback timer (low frequency safety net)
    # ========================================================

    def setup_fallback_timer(self):
        """Low-frequency safety net.

        Events are the primary driver. This timer only exists to:
          * retry embedding while we are not yet attached to the taskbar,
          * recover if an event was missed (e.g. Explorer restart racing).
        """
        self.fallback_timer = QTimer(self)
        self.fallback_timer.timeout.connect(self._fallback_tick)
        self.fallback_timer.start(1000)

    def _fallback_tick(self):
        if not self._embedded:
            self.embed_to_taskbar()
        else:
            self._revalidate_taskbar()
            self.reposition_widget()

    # ========================================================
    # Taskbar discovery
    # ========================================================

    def find_taskbar(self):
        self.taskbar = win32gui.FindWindow("Shell_TrayWnd", None)
        if not self.taskbar:
            return False

        # Win10 hierarchy:
        #   Shell_TrayWnd
        #     └─ ReBarWindow32
        #          └─ MSTaskSwWClass
        self.taskrebar = find_window_class(self.taskbar, "ReBarWindow32")
        self.taskbar_view = find_window_class(self.taskrebar, "MSTaskSwWClass")
        self.tray = find_window_class(self.taskbar, "TrayNotifyWnd")

        return bool(self.taskbar_view and self.tray)

    # ========================================================
    # Embed into taskbar
    # ========================================================

    def embed_to_taskbar(self):
        if self._embedded:
            self.reposition_widget()
            return

        if not self.find_taskbar():
            # Taskbar not ready yet; the fallback timer will retry.
            return

        hwnd = int(self.winId())

        # Strip popup-style styles and make the widget a child window.
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        style &= ~win32con.WS_POPUP
        style &= ~win32con.WS_OVERLAPPED
        style &= ~win32con.WS_CAPTION
        style &= ~win32con.WS_THICKFRAME
        style &= ~win32con.WS_MINIMIZEBOX
        style &= ~win32con.WS_MAXIMIZEBOX
        style &= ~win32con.WS_SYSMENU
        style |= win32con.WS_CHILD
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

        # Reparent under the taskbar rebar.
        win32gui.SetParent(hwnd, self.taskrebar)

        self._embedded = True
        self.reposition_widget()
        self.show()

    # ========================================================
    # Reposition widget
    # ========================================================

    def reposition_widget(self):
        if not self._embedded:
            return

        # Guard against re-entrancy: MoveWindow below can itself generate
        # LOCATIONCHANGE events for the taskbar view, which would call us
        # again and again.
        if self._in_reposition:
            return
        self._in_reposition = True
        try:
            self._reposition_widget_impl()
        finally:
            self._in_reposition = False

    def _reposition_widget_impl(self):
        hwnd = int(self.winId())
        if not win32gui.IsWindow(hwnd):
            return

        # Verify all tracked HWNDs are still alive.
        if not win32gui.IsWindow(self.taskbar):
            self._embedded = False
            self.embed_to_taskbar()
            return
        if not win32gui.IsWindow(self.taskbar_view):
            self._embedded = False
            self.embed_to_taskbar()
            return
        if not win32gui.IsWindow(self.tray):
            self._embedded = False
            self.embed_to_taskbar()
            return

        # Screen-space rectangles.
        taskbar_rect = win32gui.GetWindowRect(self.taskbar)
        view_rect = win32gui.GetWindowRect(self.taskbar_view)
        tray_rect = win32gui.GetWindowRect(self.tray)

        taskbar_left, taskbar_top, taskbar_right, taskbar_bottom = taskbar_rect
        view_left, view_top, view_right, view_bottom = view_rect
        tray_left, tray_top, tray_right, tray_bottom = tray_rect

        width = self.WIDTH
        height = tray_bottom - tray_top

        # Target screen position: widget sits immediately left of the tray.
        #
        #        Widget      Tray
        #     ┌──────────┐┌──────────
        #     │          ││
        #     └──────────┘└──────────
        #                 ↑
        #              tray_left
        screen_x = tray_left - width
        screen_y = tray_top

        # Convert screen coordinates to view-client coordinates.
        x = screen_x - view_left
        y = screen_y - view_top

        if self.last_position == (x, y):
            return
        self.last_position = (x, y)

        # Shrink the taskbar view so the widget occupies real space.
        parent = win32gui.GetParent(self.taskbar_view)
        parent_cx, parent_cy = win32gui.ClientToScreen(parent, (0, 0))

        rel_x = view_left - parent_cx
        rel_y = view_top - parent_cy

        win32gui.MoveWindow(
            self.taskbar_view,
            rel_x,
            rel_y,
            view_right - view_left - self.WIDTH,  # reserve space
            view_bottom - view_top,               # keep height
            True,
        )

        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOP,
            x,
            y,
            width,
            height,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )

    # ========================================================
    # Mouse / menu
    # ========================================================

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            menu.setStyleSheet("""
                    QMenu {
                        background-color: #f0f0f0;
                        color: #000000;
                        border: 1px solid #aaaaaa;
                        border-radius: 6px;
                        padding: 2px;
                        font-size: 12px;
                    }
                    QMenu::item {
                        background-color: transparent;
                        padding: 4px 16px;
                        border-radius: 4px;
                        margin: 1px 2px;
                    }
                    QMenu::item:selected {
                        background-color: #2BD67B;
                        color: #ffffff;
                    }
                """)
            # Frameless + translucent background so border-radius renders.
            menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            menu.setWindowFlags(
                menu.windowFlags() |
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.NoDropShadowWindowHint
            )

            exit_action = QAction("Exit", menu)
            exit_action.triggered.connect(QApplication.instance().quit)
            menu.addAction(exit_action)

            # Show above the cursor if there is not enough room below.
            pos = event.globalPosition().toPoint()
            size = menu.sizeHint()
            screen = self.screen().availableGeometry()

            x = pos.x()
            y = pos.y() - size.height()

            # Clamp to screen bounds.
            if x + size.width() > screen.right():
                x = screen.right() - size.width()
            if x < screen.left():
                x = screen.left()
            if y < screen.top():
                y = screen.top()

            menu.exec(QPoint(x, y))
            event.accept()
            return

        super().mousePressEvent(event)

    # ========================================================
    # Shutdown
    # ========================================================

    def closeEvent(self, event):
        # Stop timers.
        self.timer.stop()
        if hasattr(self, "fallback_timer"):
            self.fallback_timer.stop()

        # Remove the WinEvent hook.
        if self._win_event_hook:
            user32.UnhookWinEvent(self._win_event_hook)
            self._win_event_hook = None
        self._win_event_cb = None

        # Restore the taskbar view's original width.
        if self.taskbar_view and win32gui.IsWindow(self.taskbar_view):
            rect = win32gui.GetWindowRect(self.taskbar_view)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            parent = win32gui.GetParent(self.taskbar_view)
            pcx, pcy = win32gui.ClientToScreen(parent, (0, 0))
            win32gui.MoveWindow(
                self.taskbar_view,
                rect[0] - pcx, rect[1] - pcy,
                w + self.WIDTH, h,
                True,
            )

        event.accept()


# ============================================================
# Main
# ============================================================

def main():
    args = parse_args()

    # Enable DPI awareness before any window is created.
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    _KEY = "TokenNetSpeedWidget_SingleInstance"

    # Single-instance guard.
    socket = QLocalSocket()
    socket.connectToServer(_KEY)
    if socket.waitForConnected(200):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(None, "Notify", "Netspeed is running!")
        sys.exit(0)

    QLocalServer.removeServer(_KEY)
    server = QLocalServer()
    if not server.listen(_KEY):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(None, "Notify",
                            f"listen failed: {server.errorString()}")
        sys.exit(1)

    w = NetSpeedWidget(args)
    w.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()