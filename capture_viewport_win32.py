"""Capture IPG-MOVIE OpenGL viewport via Win32 PrintWindow API.

Replaces the broken Tcl gl readpixels → Tk photo → PNG path which
corrupts the PNG IHDR width field (always becomes 0x49484452 = 'IHDR').

Usage:
    from capture_viewport_win32 import capture_ipgmovie_viewport
    capture_ipgmovie_viewport(Path("output.png"))
"""

import ctypes
from pathlib import Path
from typing import Optional, Tuple


def _find_ipgmovie_hwnd() -> Optional[int]:
    """Find the main IPG-MOVIE window handle."""
    import win32gui

    candidates: list = []

    def _enum(hwnd, ctx):
        title = win32gui.GetWindowText(hwnd)
        if "IPGMovie" in title:
            ctx.append(hwnd)
        return True

    win32gui.EnumWindows(_enum, candidates)
    return candidates[0] if candidates else None


def _find_ogl_viewport(parent_hwnd: int) -> Optional[Tuple[int, int, int]]:
    """Find Tk-OGL widget (real viewport, not 1x1 placeholder).

    Returns (hwnd, width, height) or None.
    """
    import win32gui

    children: list = []

    def _enum(hwnd, ctx):
        if win32gui.GetClassName(hwnd) == "Tk-OGL":
            rect = win32gui.GetClientRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w >= 640:  # Real viewport, not 1x1 placeholder
                ctx.append((hwnd, w, h))
        return True

    win32gui.EnumChildWindows(parent_hwnd, _enum, children)

    if children:
        return children[0]
    return None


def capture_ipgmovie_viewport(out_path: Path) -> None:
    """Capture IPG-MOVIE's OpenGL viewport and save as PNG.

    Uses PrintWindow with PW_RENDERFULLCONTENT to capture the
    Tk-OGL widget, bypassing the broken Tcl gl readpixels path.

    Raises RuntimeError if the capture fails (window not found,
    PrintWindow fails, etc).
    """
    import win32gui
    import win32ui
    from PIL import Image

    PW_RENDERFULLCONTENT = 3
    PW_LIB = ctypes.windll.user32

    # 1. Find IPG-MOVIE window
    movie_hwnd = _find_ipgmovie_hwnd()
    if movie_hwnd is None:
        raise RuntimeError("IPG-MOVIE window not found")

    # 2. Find the actual OpenGL viewport widget
    ogl_info = _find_ogl_viewport(movie_hwnd)
    if ogl_info is None:
        raise RuntimeError(
            "IPG-MOVIE OpenGL viewport (Tk-OGL) not found; "
            "is IPG-MOVIE running with an active scene?"
        )

    hwnd, width, height = ogl_info

    # 3. Capture via PrintWindow
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    try:
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)

        ok = PW_LIB.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not ok:
            raise RuntimeError(f"PrintWindow failed (hwnd={hwnd})")

        bits = bitmap.GetBitmapBits(True)

        # Convert BGRA → RGBA, flip Y axis, save as PNG
        img = Image.frombuffer("RGBA", (width, height), bits, "raw", "BGRA", 0, 1)
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
        img.save(out_path, "PNG")
    finally:
        _cleanup_gdi(hwnd, hwnd_dc, bitmap, save_dc, mfc_dc)


def capture_ipgmovie_viewport_bytes() -> bytes:
    """Capture IPG-MOVIE viewport and return PNG bytes.

    Same as capture_ipgmovie_viewport() but returns bytes instead
    of writing to file.
    """
    import io
    import win32gui
    import win32ui
    from PIL import Image

    PW_RENDERFULLCONTENT = 3
    PW_LIB = ctypes.windll.user32

    movie_hwnd = _find_ipgmovie_hwnd()
    if movie_hwnd is None:
        raise RuntimeError("IPG-MOVIE window not found")

    ogl_info = _find_ogl_viewport(movie_hwnd)
    if ogl_info is None:
        raise RuntimeError("IPG-MOVIE OpenGL viewport not found")

    hwnd, width, height = ogl_info

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    try:
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)

        ok = PW_LIB.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not ok:
            raise RuntimeError(f"PrintWindow failed (hwnd={hwnd})")

        bits = bitmap.GetBitmapBits(True)
        img = Image.frombuffer("RGBA", (width, height), bits, "raw", "BGRA", 0, 1)
        img = img.transpose(Image.FLIP_TOP_BOTTOM)

        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    finally:
        _cleanup_gdi(hwnd, hwnd_dc, bitmap, save_dc, mfc_dc)


def _cleanup_gdi(
    hwnd: int,
    hwnd_dc: int,
    bitmap: object,
    save_dc: object,
    mfc_dc: object,
) -> None:
    """Safely release GDI resources, ignoring individual errors."""
    import win32gui

    errors = []
    for name, cleanup in [
        ("DeleteObject(bitmap)", lambda: win32gui.DeleteObject(bitmap.GetHandle())),
        ("save_dc.DeleteDC", save_dc.DeleteDC),
        ("mfc_dc.DeleteDC", mfc_dc.DeleteDC),
        ("ReleaseDC", lambda: win32gui.ReleaseDC(hwnd, hwnd_dc)),
    ]:
        try:
            cleanup()
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if errors:
        import logging

        logging.getLogger(__name__).warning(
            "GDI cleanup errors: %s", "; ".join(errors)
        )
