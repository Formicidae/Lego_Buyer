import json, sys, os
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.path.dirname(__file__))
from match import crop, norm, render
from assign import assign
jpath, pdf, outdir = sys.argv[1:4]
d=json.load(open(jpath)); m=json.load(open(os.path.join(outdir,'match.json')))
got,cap=assign(m)
left=[e for e,q in cap.items() if q>0]
invbox={e['element']:((e.get('pdf',pdf),e['page']),e['bbox']) for e in d['inventory'] if e['bbox']}
first={}
for s in d['steps']:
    for c in s['callouts']:
        if c['key'] is not None and c['bbox'] is not None and str(c['key']) not in first: first[str(c['key'])]=(s['page'],c['bbox'])
pngs=render(pdf,[p for p,_ in invbox.values()]+[p for p,_ in first.values()],os.path.join(outdir,'pages'))
un=[o for o in m['callouts'] if o not in got]
items=[('inv',e,f"{e} {cap[e]}x left") for e in left]+[('co',o,f"callout obj {o} {m['callouts'][o]['qty']}x") for o in un]
per=8; cw=120; ch=145
rows=(len(items)+per-1)//per
sheet=Image.new('RGB',(per*cw,rows*ch),'white'); dr=ImageDraw.Draw(sheet); f=ImageFont.load_default()
for i,(kind,key,label) in enumerate(items):
    r,c=divmod(i,per); x=c*cw; y=r*ch
    pg,bb=invbox[key] if kind=='inv' else first[key]
    n,_=norm(crop(pngs[pg],bb,pad=2)); sheet.paste(n.resize((cw-8,cw-8)),(x+4,y+4))
    dr.text((x+4,y+cw-2),label,fill='black' if kind=='inv' else 'red',font=f)
sheet.save(os.path.join(outdir,'leftovers.png')); print(sheet.size, len(left), 'left,', len(un), 'unassigned')
