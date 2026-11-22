"""Optional bounded text extraction for public, explicitly approved PDFs.

Uses the system poppler `pdftotext` utility if installed; no dynamic package
installation or OCR. Scanned/image-only PDFs remain metadata_only.
"""

import asyncio
import shutil

PDF_MAX_INPUT = 2 * 1024 * 1024
PDF_MAX_TEXT = 300_000
PDF_TIMEOUT = 8


async def pdf_text(raw: bytes, *, robots: str = "") -> str | None:
    """Return genuine PDF text or None without risking fabricated quotations."""
    if (
        len(raw) > PDF_MAX_INPUT
        or not raw.startswith(b"%PDF-")
        or any(flag in robots.casefold() for flag in ("noarchive", "nosnippet", "noindex"))
        or shutil.which("pdftotext") is None
    ):
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            "pdftotext", "-f", "1", "-l", "10", "-nopgbrk", "-enc", "UTF-8", "-", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(raw), timeout=PDF_TIMEOUT)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return None
    except (OSError, ValueError):
        return None
    if process.returncode != 0 or len(output) > PDF_MAX_TEXT:
        return None
    text = output.decode("utf-8", errors="replace").replace("\x00", " ").strip()
    return text if len(text) >= 100 and any(char.isalnum() for char in text) else None
