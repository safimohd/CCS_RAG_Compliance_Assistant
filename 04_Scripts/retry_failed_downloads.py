"""
retry_failed_downloads.py - retries failed PDF downloads, uses short hash filenames
"""
import csv, hashlib, time, requests
from pathlib import Path

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0","Accept": "application/pdf,*/*","Referer": "https://rera.karnataka.gov.in/"})
TIMEOUT = 60
DELAY = 1.5

def sha8(t): return hashlib.md5(t.encode()).hexdigest()[:8]

def download(url, dest):
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = SESSION.get(url, timeout=TIMEOUT, stream=True)
        r.raise_for_status()
        if "html" in r.headers.get("Content-Type","") and b"<html" in r.content[:200].lower():
            return False, "Got HTML (login wall)"
        with open(dest,"wb") as f:
            for chunk in r.iter_content(65536): f.write(chunk)
        s = dest.stat().st_size
        if s < 500: dest.unlink(); return False, f"Too small ({s}b)"
        return True, f"OK ({s:,} bytes)"
    except Exception as e: return False, str(e)[:300]

def retry_csv(csv_path, url_col, status_col, filepath_col, base_dir):
    with open(csv_path, newline="", encoding="utf-8-sig") as f: rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys())
    failed = [r for r in rows if r[status_col].lower() not in ("downloaded","ok")]
    print(f"\n{csv_path}\n  Total: {len(rows)}  Failed: {len(failed)}")
    ok_n = fail_n = 0
    for i,r in enumerate(failed):
        url = r[url_col].strip()
        if not url: continue
        dest = Path(base_dir) / (sha8(url)+".pdf")
        print(f"  [{i+1}/{len(failed)}] {r.get('title',url)[:70]}")
        ok,msg = download(url,dest)
        if ok: r[status_col]="downloaded"; r[filepath_col]=str(dest); ok_n+=1; print(f"    OK {msg}")
        else: r[status_col]=f"failed:{msg[:60]}"; fail_n+=1; print(f"    FAIL {msg}")
        time.sleep(DELAY)
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fieldnames); w.writeheader(); w.writerows(rows)
    print(f"  Result: {ok_n} recovered, {fail_n} still failed")

def retry_hc(csv_path, base_dir):
    with open(csv_path, newline="", encoding="utf-8-sig") as f: rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys())
    failed = [r for r in rows if not r["status"].startswith("Downloaded")]
    print(f"\n{csv_path}\n  Total: {len(rows)}  Failed: {len(failed)}")
    ok_n = fail_n = 0
    for i,r in enumerate(failed):
        url = r.get("url","").strip()
        if not url or not url.startswith("http"): continue
        title = r.get("title",r.get("filename",url))[:60]
        folder = r.get("folder","").strip() or "misc"
        dest = Path(base_dir) / folder / (sha8(url)+".pdf")
        print(f"  [{i+1}/{len(failed)}] {title}")
        ok,msg = download(url,dest)
        if ok: r["status"]="Downloaded"; r["local_file"]=str(dest); ok_n+=1; print(f"    OK {msg}")
        else: r["status"]=f"FAILED:{msg[:60]}"; fail_n+=1; print(f"    FAIL {msg}")
        time.sleep(DELAY)
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fieldnames); w.writeheader(); w.writerows(rows)
    print(f"  Result: {ok_n} recovered, {fail_n} still failed")

if __name__=="__main__":
    root = Path(__file__).parent.parent
    print("="*60+"\nRETRY FAILED PDF DOWNLOADS\n"+"="*60)
    retry_csv(str(root/"03_Metadata/RERA/krera_metadata.csv"),"url","status","filepath",str(root/"02_Corpus/RERA/KRERA_Documents"))
    retry_csv(str(root/"03_Metadata/RERA/rera_master_index.csv"),"url","status","local_path",str(root/"02_Corpus/RERA"))
    retry_hc(str(root/"03_Metadata/Healthcare/healthcare_master_index.csv"),str(root/"02_Corpus/Healthcare"))
    print("\n"+"="*60+"\nDone.\n"+"="*60)
