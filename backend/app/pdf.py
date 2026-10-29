"""Render a report as a PDF.

Server-side rather than in the browser, for three reasons: the same document comes out on
every machine, it can be attached to an email without a browser in the loop, and the data
never has to leave the server to be laid out.

WeasyPrint is imported lazily. It pulls in cairo and pango and takes about a second, which is
a second added to every reload during development for a feature most requests never touch.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parent / "templates"

_environment = Environment(
    loader=FileSystemLoader(TEMPLATES),
    # Report content includes free text the user typed. Autoescaping is what stops that from
    # becoming markup in the rendered document.
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


class PdfUnavailableError(RuntimeError):
    """WeasyPrint couldn't load — almost always missing system libraries."""


def _format_pay(pay: dict[str, Any]) -> str | None:
    """Mirrors the browser's formatting. Kept simple rather than shared: the two render in
    different runtimes, and a shared formatter would mean shipping one to both."""
    currency = pay.get("currency") or "USD"
    symbol = "$" if currency == "USD" else f"{currency} "

    def money(amount: int) -> str:
        return f"{symbol}{amount:,.0f}"

    low, median, high = pay.get("low"), pay.get("median"), pay.get("high")
    if low is not None and high is not None:
        return f"{money(low)} – {money(high)}"
    if median is not None:
        return f"{money(median)} median"
    if low is not None:
        return f"from {money(low)}"
    if high is not None:
        return f"up to {money(high)}"
    return None


def _render_html(payload: dict[str, Any], generated_at: datetime) -> str:
    traits = payload.get("traits", {})
    careers = []
    for career in payload.get("careers", []):
        prepared = dict(career)
        if career.get("pay"):
            prepared["pay_text"] = _format_pay(career["pay"])
        careers.append(prepared)

    return _environment.get_template("report.html").render(
        code_labels=[
            traits.get(trait, {}).get("label", trait) for trait in payload.get("holland_code", [])
        ],
        traits=traits,
        strengths=payload.get("strengths", []),
        families=payload.get("families", []),
        careers=careers,
        challenges=payload.get("challenges", []),
        generated_at=generated_at.strftime("%-d %B %Y"),
    )


def _render_pdf_sync(payload: dict[str, Any], generated_at: datetime) -> bytes:
    try:
        from weasyprint import HTML  # noqa: PLC0415 — see the module docstring
    except OSError as error:  # pragma: no cover - depends on the host's system libraries
        raise PdfUnavailableError(
            "WeasyPrint could not load its system dependencies (cairo, pango). "
            "On Debian/Ubuntu: apt install libpango-1.0-0 libpangoft2-1.0-0."
        ) from error

    return HTML(string=_render_html(payload, generated_at)).write_pdf()


async def render_report_pdf(payload: dict[str, Any], generated_at: datetime) -> bytes:
    """Lay out a report. Runs off the event loop — rendering is CPU-bound and blocking."""
    return await asyncio.to_thread(_render_pdf_sync, payload, generated_at)
