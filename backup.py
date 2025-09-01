import os, re, time, json, sys
from pathlib import Path
import requests
from tqdm import tqdm

API = "https://api.flat.io/v2"
PAUSE = 0.25
TIMEOUT = 120

FORMATS = [f.strip().lower() for f in os.getenv("FORMATS","pdf,musicxml,midi").split(",") if f.strip()]
EXT = {"pdf":"pdf","mp3":"mp3","wav":"wav","midi":"mid","musicxml":"musicxml","xml":"xml","png":"png","svg":"svg"}

def H(tok): return {"Authorization": f"Bearer {tok}", "Accept":"application/json"}

def ok(r):
    r.raise_for_status();
    return r

def sanitize(s): return re.sub(r'[\\/*?:"<>|]+', "_", s or "").strip() or "untitled"

def paged(session, url, params=None):
    page=1
    while True:
        q={"page":page,"perPage":100}
        if params: q.update(params)
        r=session.get(url, params=q, timeout=TIMEOUT)
        if r.status_code==403: return  # forbidden path, stop this iterator
        ok(r)
        data=r.json()
        if not data: break
        for item in data: yield item
        page+=1; time.sleep(PAUSE)

def list_scores(session):
    seen=set(); out=[]
    # Try root scores (ok if 403/empty)
    for s in paged(session, f"{API}/collections/root/scores"):
        if s["id"] not in seen:
            seen.add(s["id"]); out.append({"id":s["id"],"title": s.get("title")})
    # Walk every collection we can see (owned or shared)
    for col in paged(session, f"{API}/collections"):
        cid = col.get("id")
        if not cid:
            continue
        for s in paged(session, f"{API}/collections/{cid}/scores"):
            if s["id"] not in seen:
                seen.add(s["id"]); out.append({"id":s["id"],"title": s.get("title")})
    return out

def latest_rev(session, sid):
    r=ok(session.get(f"{API}/scores/{sid}/revisions", timeout=TIMEOUT)).json()
    if not r: raise RuntimeError("No revisions")
    r.sort(key=lambda x: x.get("creationDate",""), reverse=True)
    return r[0]["id"]

def fetch(session, url, dest):
    r=session.get(url, params={"url":"true"}, timeout=TIMEOUT)
    if r.headers.get("Content-Type","").startswith("application/json"):
        try:
            u=r.json().get("url")
            if u:
                rr=requests.get(u, stream=True, timeout=TIMEOUT); rr.raise_for_status()
                with open(dest,"wb") as f:
                    for chunk in rr.iter_content(8192):
                        if chunk: f.write(chunk)
                return True
        except Exception: pass
    # fallback: direct bytes
    r=session.get(url, headers={"Accept":"*/*"}, timeout=TIMEOUT)
    if r.ok:
        with open(dest,"wb") as f: f.write(r.content)
        return True
    return False

def main():
    tok=os.getenv("FLAT_API_TOKEN")
    if not tok:
        print("FLAT_API_TOKEN not set", file=sys.stderr); sys.exit(1)
    out=Path("out"); out.mkdir(exist_ok=True)

    s=requests.Session(); s.headers.update(H(tok))
    print("Enumerating scores…", flush=True)

    # sanity: token valid?
    ok(s.get(f"{API}/me", timeout=TIMEOUT))

    scores=list_scores(s)
    print("First score:", scores[0])
sid = scores[0]["id"]
rev = latest_rev(sess, sid)
print("Latest rev:", rev)
url = f"{API}/scores/{sid}/revisions/{rev}/pdf"
print("Trying export:", url)
r = sess.get(url, params={"url":"true"}, timeout=TIMEOUT)
print("Status:", r.status_code, r.text[:300])
sys.exit(0)

    if not scores:
        print("No scores found."); return
    print(f"Found {len(scores)} scores. Exporting: {', '.join(FORMATS)}", flush=True)

    manifest=[]; failures=[]
    for sc in tqdm(scores, desc="Scores"):
        sid=sc["id"]; title=sanitize(sc.get("title"))
        try:
            rev=latest_rev(s, sid)
        except Exception as e:
            failures.append([title,sid,"*",f"revisions: {e}"]); continue
        saved=[]
        for fmt in FORMATS:
            ext=EXT[fmt]; api_fmt="musicxml" if fmt=="xml" else fmt
            dest=out/f"{title}__{sid}.{ext}"
            if dest.exists(): saved.append(str(dest)); continue
            url=f"{API}/scores/{sid}/revisions/{rev}/{api_fmt}"
            try:
                if fetch(s, url, dest): saved.append(str(dest))
                else: failures.append([title,sid,fmt,"400/404 or unsupported"])
            except requests.HTTPError as e:
                failures.append([title,sid,fmt,f"HTTP {getattr(e.response,'status_code','?')}"])
            except Exception as e:
                failures.append([title,sid,fmt,str(e)])
            time.sleep(PAUSE)
        manifest.append({"id":sid,"title":sc.get("title"),"files":saved})
    (out/"manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if failures:
        with open(out/"failures.csv","w",encoding="utf-8") as f:
            f.write("title,score_id,format,reason\n")
            for r in failures: f.write(",".join(['"'+str(x).replace('"','""')+'"' for x in r])+"\n")
    print("Done. See ./out/, manifest.json, and failures.csv")

if __name__=="__main__":
    main()
