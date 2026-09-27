import requests
from pathlib import Path
import hashlib

URL = "https://www.indiacode.nic.in/bitstream/123456789/2158/4/A2016-16.pdf"

OUT = Path("RealEstate_RAG_Raw") / "01_RERA_Act"
OUT.mkdir(parents=True, exist_ok=True)

FILE = OUT / "Real_Estate_Regulation_and_Development_Act_2016.pdf"

if FILE.exists():
    with FILE.open("rb") as f:
        if f.read(5) == b"%PDF-":
            print(f"Already downloaded: {FILE}")
            raise SystemExit(0)
    FILE.unlink()

print("Downloading RERA Act 2016 from official India Code PDF source...")
print(URL)

headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/pdf,*/*;q=0.8",
}

r = requests.get(
    URL,
    headers=headers,
    timeout=(20, 120),
    stream=True,
)

r.raise_for_status()

first = b""
total = 0

tmp = FILE.with_suffix(".pdf.part")

with tmp.open("wb") as f:
    for chunk in r.iter_content(chunk_size=256 * 1024):
        if not chunk:
            continue

        if not first:
            first = chunk[:16]
            if first[:5] != b"%PDF-":
                raise RuntimeError(
                    f"Response is not a PDF. Prefix={first!r}, "
                    f"Content-Type={r.headers.get('Content-Type')!r}"
                )

        total += len(chunk)
        f.write(chunk)

if first[:5] != b"%PDF-":
    tmp.unlink(missing_ok=True)
    raise RuntimeError("Missing PDF signature.")

tmp.replace(FILE)

h = hashlib.sha256()
with FILE.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)

print()
print("SUCCESS")
print(f"File      : {FILE}")
print(f"Size      : {total:,} bytes")
print(f"SHA-256   : {h.hexdigest()}")
print("PDF check : PASSED (%PDF-)")
