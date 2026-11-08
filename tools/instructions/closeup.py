import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from match import crop, render
from PIL import Image, ImageDraw
jpath, pdf, outdir = sys.argv[1:4]; items = sys.argv[4:]
d=json.load(open(jpath))
first={}
for s in d['steps']:
    for c in s['callouts']:
        if c['key'] is not None and c['bbox'] is not None and str(c['key']) not in first: first[str(c['key'])]=(s['page'],c['bbox'],s['num'],c['qty'])
invbox={e['element']:((e.get('pdf',pdf),e['page']),e['bbox'],e['qty']) for e in d['inventory'] if e['bbox']}
pngs=render(pdf,[v[0] for v in first.values()]+[v[0] for v in invbox.values()],os.path.join(outdir,'pages'))
ims=[]
for k in items:
    if k.startswith('co:'):
        k=k[3:]; pg,bb,num,q=first[k]; lab=f"obj {k} p{pg} step {num} {q}x"
    else:
        pg,bb,q=invbox[k]; lab=f"el {k} ({q}x)"
    im=crop(pngs[pg],bb,pad=4)
    sc=max(1,int(160/max(im.size))); im=im.resize((im.width*sc,im.height*sc)) if sc>1 else im
    ims.append((lab,im))
W=sum(i.width+12 for _,i in ims); H=max(i.height for _,i in ims)+22
out=Image.new('RGB',(W,H),'white'); x=0; dr=ImageDraw.Draw(out)
for k,im in ims: out.paste(im,(x,0)); dr.text((x,H-14),k,fill='black'); x+=im.width+12
out.save(os.path.join(outdir,'closeup.png')); print(out.size)
