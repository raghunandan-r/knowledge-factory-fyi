"""Download CMS's documentation for each source and extract it to text.

PDFs land in data/docs/, their text in data/docs/text/<name>.md (one file per doc).
Files already downloaded are kept; delete data/docs/ to refetch.

Usage: uv run python -m ingest.fetch_docs
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from pypdf import PdfReader

from ingest.common import ROOT, download, http_client, load_config
from ingest.sync_nppes import WEEKLY_RE, mmddyy


def pdf_to_markdown(pdf: Path, name: str, source: str) -> str:
    reader = PdfReader(pdf)
    parts = [f"# {name}\n\nSource: {source}\nPages: {len(reader.pages)}\n"]
    for i, page in enumerate(reader.pages, 1):
        parts.append(f"\n## Page {i}\n\n{(page.extract_text() or '').strip()}\n")
    return "".join(parts)


def fetch_nppes(cfg: dict, client, out: Path) -> dict[str, tuple[Path, str]]:
    """Pull the readme PDFs out of the smallest NPPES zip (a weekly update)."""
    r = client.get(cfg["nppes"]["index_url"])
    r.raise_for_status()
    weeklies = {
        (mmddyy(b), f"NPPES_Data_Dissemination_{a}_{b}_Weekly_V2.zip")
        for a, b in WEEKLY_RE.findall(r.text)
    }
    if not weeklies:
        raise RuntimeError("no weekly NPPES zip on the index page")
    zip_name = max(weeklies)[1]
    url = cfg["nppes"]["base_url"] + zip_name
    wanted = cfg["docs"]["nppes_zip_members"]
    done = {n: out / f"{n}.pdf" for n in wanted}
    if all(p.exists() for p in done.values()):
        return {n: (p, url) for n, p in done.items()}
    zpath = download(client, url, out / "raw" / zip_name, cfg["http"]["retries"])
    with zipfile.ZipFile(zpath) as z:
        names = {Path(i.filename).name: i for i in z.infolist()}
        for doc, member in wanted.items():
            if member not in names:
                raise RuntimeError(f"{zip_name}: no member {member!r}; has {sorted(names)}")
            done[doc].write_bytes(z.read(names[member]))
    zpath.unlink()
    return {n: (p, url) for n, p in done.items()}


def main() -> int:
    cfg = load_config()
    out = ROOT / cfg["docs"]["dir"]
    text_dir = out / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    docs: dict[str, tuple[Path, str]] = {}
    with http_client(cfg) as client:
        for name, url in cfg["docs"]["files"].items():
            docs[name] = (download(client, url, out / f"{name}.pdf", cfg["http"]["retries"]), url)
        docs |= fetch_nppes(cfg, client, out)
    for name, (pdf, url) in docs.items():
        md = pdf_to_markdown(pdf, name, url)
        (text_dir / f"{name}.md").write_text(md)
        print(f"  {name}: {pdf.stat().st_size / 1e6:.1f} MB pdf -> {len(md) / 1e3:.0f}k chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
