"""Dispatch slip rendering — one HTML source for preview, printing and PDF.

The slip is the whole order details printed when a dispatch completes:
order header (order no, customer, PO, batch, job), every loaded box with
its weight/pieces, and totals. Qt-free so tests can assert on it without a
display; the dialog in factory/ui/label_preview.py prints this same HTML
through QTextDocument (office printer) or saves it as PDF.
"""
from __future__ import annotations

import html as _html


def _esc(value) -> str:
    return _html.escape("" if value is None else str(value))


def render_html(slip: dict) -> str:
    """Full dispatch-slip document as printable HTML."""
    rows = slip.get("rows") or []
    batches = slip.get("batches") or []
    unit = str(slip.get("piece_unit") or "reel")
    # Kg products have no piece count: the coil-count column stays "Coils"
    # (never "Kg" — that column counts coils, not weight) and the Pieces
    # column stays blank.
    is_wrapper = unit.lower() == "wrapper"
    is_kg = unit.lower() == "kg"
    coil_word = "Wrappers" if is_wrapper else "Coils"
    rp = slip.get("required_pieces")
    rq = slip.get("required_weight_kg")
    if rp is not None:
        target = (f"{int(rp):,} {slip.get('pieces_word') or 'pcs'}"
                  f" in {int(slip.get('required_bundles') or 0)} boxes")
    elif rq is not None:
        try:
            target = f"{float(rq):.3f} kg in {int(slip.get('loaded_boxes') or 0)} boxes"
        except (TypeError, ValueError):
            target = f"{rq} kg"
    else:
        target = f"{int(slip.get('required_bundles') or 0)} boxes"
    body = []
    for r in rows:
        code = str(r.get("job_code") or "").strip().upper()
        pcs = int(r.get("pieces") or 0) if is_wrapper else 0
        body.append(
            "<tr>"
            f"<td>{_esc(r.get('box_no'))}</td>"
            f"<td>{_esc(code)}</td>"
            f"<td>{_esc(r.get('carton_no'))}</td>"
            f"<td>{int(r.get('num_reels') or 0)}</td>"
            f"<td>{f'{pcs:,}' if pcs else ''}</td>"
            f"<td>{float(r.get('gross') or 0):.3f}</td>"
            f"<td>{float(r.get('net') or 0):.3f}</td>"
            f"<td>{_esc(r.get('scanned_at'))}</td>"
            "</tr>")
    if not body:
        body.append('<tr><td colspan="8">No boxes.</td></tr>')
    batch_rows = []
    for b in batches:
        bpcs = int(b.get("pieces") or 0) if is_wrapper else 0
        batch_rows.append(
            "<tr>"
            f"<td>{_esc(b.get('label') or '?')}</td>"
            f"<td>{int(b.get('boxes') or 0)}</td>"
            f"<td>{f'{bpcs:,}' if bpcs else ''}</td>"
            f"<td>{float(b.get('gross') or 0):.3f}</td>"
            f"<td>{float(b.get('net') or 0):.3f}</td>"
            "</tr>")
    batch_block = ""
    if batch_rows:
        batch_block = (
            "<br/><table border=\"1\" cellspacing=\"0\" cellpadding=\"4\">"
            "<tr><th>Batch (job code)</th><th>Boxes</th><th>Pieces</th>"
            "<th>Gross · kg</th><th>Net · kg</th></tr>"
            f"{''.join(batch_rows)}"
            "</table>")
    frozen = bool(slip.get("frozen"))
    stamp = ("Saved record — frozen at completion. Reprints are identical."
             if frozen else "Live view — order not completed yet.")
    printed = slip.get("print_count") or 0
    if is_kg:
        loaded_txt = f"{int(slip.get('loaded_boxes') or 0)} boxes"
    else:
        loaded_txt = (f"{int(slip.get('loaded_boxes') or 0)} boxes · "
                      f"{int(slip.get('loaded_pieces') or 0):,} "
                      f"{_esc(slip.get('pieces_word') or '')}")
    return f"""<html><body>
<h1>Dispatch slip — {_esc(slip.get('order_no'))}</h1>
<table border="1" cellspacing="0" cellpadding="4">
<tr><td><b>Customer</b></td><td>{_esc(slip.get('customer'))}</td></tr>
<tr><td><b>Customer PO</b></td><td>{_esc(slip.get('customer_po'))}</td></tr>
<tr><td><b>PO date</b></td><td>{_esc(slip.get('po_date') or '')}</td></tr>
<tr><td><b>Batch</b></td><td>{_esc(slip.get('batch_no'))}</td></tr>
<tr><td><b>Job</b></td><td>J:{_esc(slip.get('job_no'))} {_esc(slip.get('job_code') or '')} {_esc(slip.get('job_name'))}</td></tr>
<tr><td><b>Order date</b></td><td>{_esc(slip.get('order_date'))}</td></tr>
<tr><td><b>Completed</b></td><td>{_esc(slip.get('completed_at'))}</td></tr>
<tr><td><b>Target</b></td><td>{_esc(target)}</td></tr>
<tr><td><b>Loaded</b></td><td>{loaded_txt}</td></tr>
<tr><td><b>Total net</b></td><td>{float(slip.get('loaded_net') or 0):.3f} kg</td></tr>
<tr><td><b>Total gross</b></td><td>{float(slip.get('loaded_gross') or 0):.3f} kg</td></tr>
</table>
<br/>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>Box No</th><th>Batch</th><th>Box</th><th>{coil_word}</th><th>Pieces</th>
<th>Gross · kg</th><th>Net · kg</th><th>Scanned</th></tr>
{''.join(body)}
</table>
{batch_block}
<br/>
<table border="0" cellspacing="0" cellpadding="4" width="100%">
<tr>
<td>Delivered by: ____________________</td>
<td>Received by: ____________________</td>
</tr>
</table>
<p><i>{_esc(stamp)}</i></p>
<p><i>Printed ×{int(printed)}.</i></p>
</body></html>"""
