"""Impression d'une image dans une case (1/16) d'une feuille, via GDI Windows.

Chaque tache d'impression ne contient QUE la case courante : le reste de la page
est vide, donc la meme feuille peut etre rechargee 16 fois.
"""
from __future__ import annotations

from dataclasses import dataclass

import pywintypes
import win32con
import win32gui
import win32print
import win32ui
from PIL import Image, ImageWin

GRID = 4  # 4 x 4 = 16 cases


@dataclass
class PageGeometry:
    dpi_x: int
    dpi_y: int
    printable_w: int  # en pixels imprimante
    printable_h: int

    def cell_rect(self, index: int, gutter_mm: float = 2.0) -> tuple[int, int, int, int]:
        """Rectangle (gauche, haut, droite, bas) de la case `index` (0..15),
        en coordonnees de la zone imprimable. Lecture : gauche->droite, haut->bas."""
        row, col = divmod(index % (GRID * GRID), GRID)
        cw, ch = self.printable_w / GRID, self.printable_h / GRID
        gx = gutter_mm / 25.4 * self.dpi_x
        gy = gutter_mm / 25.4 * self.dpi_y
        l = int(col * cw + gx / 2)
        t = int(row * ch + gy / 2)
        r = int((col + 1) * cw - gx / 2)
        b = int((row + 1) * ch - gy / 2)
        return l, t, r, b


def _devmode(printer: str, mono: bool, draft: bool):
    h = win32print.OpenPrinter(printer)
    try:
        dm = win32print.GetPrinter(h, 2)["pDevMode"]
    finally:
        win32print.ClosePrinter(h)
    dm.Orientation = win32con.DMORIENT_PORTRAIT
    dm.Color = win32con.DMCOLOR_MONOCHROME if mono else win32con.DMCOLOR_COLOR
    if draft:
        dm.PrintQuality = win32con.DMRES_DRAFT
    dm.Fields |= win32con.DM_ORIENTATION | win32con.DM_COLOR | (win32con.DM_PRINTQUALITY if draft else 0)
    return dm


def _open_dc(printer: str, mono: bool, draft: bool):
    try:
        dm = _devmode(printer, mono, draft)
        hdc = win32gui.CreateDC("WINSPOOL", printer, dm)
    except pywintypes.error:
        # certains pilotes refusent un reglage : on retombe sur les reglages par defaut
        hdc = win32gui.CreateDC("WINSPOOL", printer, None)
    return win32ui.CreateDCFromHandle(hdc)


def geometry(printer: str) -> PageGeometry:
    dc = _open_dc(printer, False, False)
    try:
        return PageGeometry(
            dc.GetDeviceCaps(win32con.LOGPIXELSX),
            dc.GetDeviceCaps(win32con.LOGPIXELSY),
            dc.GetDeviceCaps(win32con.HORZRES),
            dc.GetDeviceCaps(win32con.VERTRES),
        )
    finally:
        dc.DeleteDC()


def print_in_cell(printer: str, img: Image.Image, index: int, title: str,
                  mono: bool = False, draft: bool = True) -> None:
    dc = _open_dc(printer, mono, draft)
    try:
        geo = PageGeometry(
            dc.GetDeviceCaps(win32con.LOGPIXELSX),
            dc.GetDeviceCaps(win32con.LOGPIXELSY),
            dc.GetDeviceCaps(win32con.HORZRES),
            dc.GetDeviceCaps(win32con.VERTRES),
        )
        rect = geo.cell_rect(index)
        dc.StartDoc(title)
        dc.StartPage()
        ImageWin.Dib(img.convert("RGB")).draw(dc.GetHandleOutput(), rect)
        dc.EndPage()
        dc.EndDoc()
    finally:
        dc.DeleteDC()


def print_calibration(printer: str) -> None:
    """Imprime de fines croix aux coins des 16 cases + leur numero (pour reperer le sens de la feuille)."""
    dc = _open_dc(printer, True, True)
    try:
        geo = PageGeometry(
            dc.GetDeviceCaps(win32con.LOGPIXELSX),
            dc.GetDeviceCaps(win32con.LOGPIXELSY),
            dc.GetDeviceCaps(win32con.HORZRES),
            dc.GetDeviceCaps(win32con.VERTRES),
        )
        dc.StartDoc("Calibration grille 4x4")
        dc.StartPage()
        pen = win32ui.CreatePen(win32con.PS_SOLID, max(1, geo.dpi_x // 150), 0)
        dc.SelectObject(pen)
        tick = geo.dpi_x // 12
        font = win32ui.CreateFont({"name": "Arial", "height": geo.dpi_y // 6})
        dc.SelectObject(font)
        for i in range(GRID * GRID):
            l, t, r, b = geo.cell_rect(i)
            for (x, y, dx, dy) in ((l, t, 1, 1), (r, t, -1, 1), (l, b, 1, -1), (r, b, -1, -1)):
                dc.MoveTo((x, y)); dc.LineTo((x + dx * tick, y))
                dc.MoveTo((x, y)); dc.LineTo((x, y + dy * tick))
            dc.TextOut(l + tick, t + tick, str(i + 1))
        dc.TextOut(geo.dpi_x // 4, geo.printable_h - geo.dpi_y // 4, "^ HAUT DE LA FEUILLE = case 1 en haut a gauche")
        dc.EndPage()
        dc.EndDoc()
    finally:
        dc.DeleteDC()


def printer_status(printer: str) -> dict:
    """Etat du spouleur (file d'attente, erreurs papier, etc.)."""
    h = win32print.OpenPrinter(printer)
    try:
        info = win32print.GetPrinter(h, 2)
        jobs = win32print.EnumJobs(h, 0, 99, 1)
    finally:
        win32print.ClosePrinter(h)
    flags = {
        win32print.PRINTER_STATUS_PAPER_OUT: "papier absent (recharge la feuille)",
        win32print.PRINTER_STATUS_PAPER_JAM: "bourrage",
        win32print.PRINTER_STATUS_OFFLINE: "hors ligne",
        win32print.PRINTER_STATUS_ERROR: "erreur",
        win32print.PRINTER_STATUS_PRINTING: "impression",
        win32print.PRINTER_STATUS_PAUSED: "en pause",
        win32print.PRINTER_STATUS_DOOR_OPEN: "capot ouvert",
        win32print.PRINTER_STATUS_TONER_LOW: "encre basse",
        win32print.PRINTER_STATUS_NO_TONER: "plus d'encre",
        win32print.PRINTER_STATUS_USER_INTERVENTION: "intervention requise",
    }
    st = info["Status"]
    return {
        "status": [txt for bit, txt in flags.items() if st & bit] or ["pret"],
        "spool_jobs": len(jobs),
    }
