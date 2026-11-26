"""Shared three-slot A4 layout for dispatch sticker preview, PDF and printing."""
import io

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter

from .hardware.labels import render_client_box
from .slip_pdf import _printer

SLOTS_PER_SHEET = 3


def sticker_images(details: list[dict]) -> list[QImage]:
    images = []
    for detail in details:
        data = io.BytesIO()
        render_client_box(detail, minimum_rows=4).save(data, "PNG")
        img = QImage.fromData(data.getvalue())
        if img.isNull():
            raise RuntimeError("Could not render a dispatch sticker.")
        images.append(img)
    return images


def _paint_sheet(painter, area: QRectF, dpi: int, stickers: list[QImage]) -> None:
    gap = 3 * dpi / 25.4
    height = (area.height() - 2 * gap) / SLOTS_PER_SHEET
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for number, image in enumerate(stickers):
        scale = min(area.width() / image.width(), height / image.height())
        width = image.width() * scale
        label_height = image.height() * scale
        x = area.x() + (area.width() - width) / 2
        y = area.y() + number * (height + gap) + (height - label_height) / 2
        painter.drawImage(QRectF(x, y, width, label_height), image)


def previews(stickers: list[QImage]) -> list[QImage]:
    dpi = 100
    mm = dpi / 25.4
    pages = []
    for start in range(0, len(stickers), SLOTS_PER_SHEET):
        page = QImage(round(210 * mm), round(297 * mm), QImage.Format.Format_RGB32)
        page.fill(Qt.GlobalColor.white)
        with QPainter(page) as painter:
            _paint_sheet(painter, QRectF(14 * mm, 14 * mm, 182 * mm, 269 * mm),
                         dpi, stickers[start:start + SLOTS_PER_SHEET])
        pages.append(page)
    return pages


def print_sheets(printer: QPrinter, stickers: list[QImage]) -> None:
    if not stickers:
        raise ValueError("There are no dispatch stickers to print.")
    if not printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4)):
        raise RuntimeError("The printer does not accept A4 paper.")
    if not printer.setPageOrientation(QPageLayout.Orientation.Portrait):
        raise RuntimeError("The printer does not accept portrait orientation.")
    printer.setFullPage(False)
    bounds = printer.pageRect(QPrinter.Unit.DevicePixel)
    dpi = printer.resolution()
    # Match preview margins, keeping any larger margins required by the driver.
    paper = printer.paperRect(QPrinter.Unit.DevicePixel)
    margin = 14 * dpi / 25.4
    printable = QRectF(0, 0, bounds.width(), bounds.height())
    nominal = QRectF(paper.x() + margin - bounds.x(), paper.y() + margin - bounds.y(),
                     paper.width() - 2 * margin, paper.height() - 2 * margin)
    area = printable.intersected(nominal)
    if area.width() <= 0 or area.height() <= 6 * dpi / 25.4:
        raise RuntimeError("The printer's A4 printable area is too small.")
    painter = QPainter(printer)
    if not painter.isActive():
        raise RuntimeError("Could not start printing. Check the selected printer.")
    try:
        for start in range(0, len(stickers), SLOTS_PER_SHEET):
            if start and not printer.newPage():
                raise RuntimeError("The printer could not start the next A4 sheet.")
            _paint_sheet(painter, area, dpi, stickers[start:start + SLOTS_PER_SHEET])
    finally:
        ended = painter.end()
    if not ended or printer.printerState() in (QPrinter.PrinterState.Error, QPrinter.PrinterState.Aborted):
        raise RuntimeError("The printer reported a failure.")


def save_pdf(path: str, stickers: list[QImage]) -> None:
    printer = _printer()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(path)
    print_sheets(printer, stickers)


def print_with_dialog(parent, stickers: list[QImage]) -> bool:
    from PySide6.QtPrintSupport import QPrintDialog, QPrinterInfo
    from PySide6.QtWidgets import QDialog

    if not QPrinterInfo.availablePrinters():
        raise RuntimeError("No office printer is installed. You can still save the A4 PDF.")
    printer = _printer()
    dialog = QPrintDialog(printer, parent)
    dialog.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, False)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    if printer.outputFormat() == QPrinter.OutputFormat.PdfFormat:
        raise RuntimeError("Choose an office printer, or use Save PDF.")
    print_sheets(printer, stickers)
    return True
