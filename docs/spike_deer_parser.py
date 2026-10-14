"""Proof-of-concept (30-min spike) for T-11. Parses 2026 IDFG general deer tables.
Usage: download seasons-rules-big-game-2026.pdf as bg26.pdf, pip install pdfplumber, run. Writes deer26.json.
Not production code: see DESIGN.md section 8 and ticket T-11."""
import pdfplumber,re,json,datetime as dt
MON={m:i for i,m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(),1)}
DR=re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})\s*[-–]\s*(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+)?(\d{1,2})")
UNIT=re.compile(r"\b(\d{1,2}[A-C]?)\b")
pdf=pdfplumber.open("bg26.pdf"); out=[]; last=''
for pn in range(10,37):
    p=pdf.pages[pn-1]; p=p.crop((40,0,p.width-40,p.height))
    for tb in p.extract_tables():
        if not tb or len(tb)<3: continue
        first=" ".join(c or "" for c in tb[0])
        if (tb[0][0] or "").startswith("Unit"):
            title=last; tb=[[title]]+tb
        else:
            title=first; last=first
        if "Deer" not in title or "Controlled" in title: continue
        tag="whitetail" if "White-tailed Deer Tag" in title else "regular"
        kind="any" if "Any Weapon" in title else "archery" if "Archery" in title else "muzzleloader" if "Muzzle" in title else "short-range" if "Short" in title else "?"
        hdr=[ (c or "").strip() for c in tb[1]]
        if not hdr or not hdr[0].startswith("Unit"): continue
        units=None
        for r in tb[2:]:
            if r[0]: units=UNIT.findall(r[0].replace("\n"," "))
            if not units: continue
            for ci,col in enumerate(hdr[1:],1):
                if ci>=len(r) or not r[ci] or col not in("Antlered","Antlerless"): continue
                cell=r[ci]
                for m in DR.finditer(cell):
                    m1,d1,m2,d2=m.groups(); m2=m2 or m1
                    out.append(dict(page=pn,tag=tag,kind=kind,sex=col.lower(),units=units,start=f"2026-{MON[m1]:02d}-{int(d1):02d}",end=f"2026-{MON[m2]:02d}-{int(d2):02d}",wt_only="White-tailed deer only" in cell[m.end():m.end()+40], youth="Youth" in cell[m.end():m.end()+40], two_point="2-point" in cell[m.end():m.end()+40], private_only="Private land" in cell[m.end():m.end()+60]))
json.dump(out,open("deer26.json","w"),indent=0)
print(len(out)); import collections; print(collections.Counter(o["kind"] for o in out))
for o in out[:6]: print(o)
