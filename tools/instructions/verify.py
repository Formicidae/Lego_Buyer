"""Side-by-side sheet: callout image | assigned inventory image, 6 pairs per row."""
import json, sys, os
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.path.dirname(__file__))
from match import crop, norm, render
from assign import assign
def main(jpath, pdf, outdir, overrides=None):
    d=json.load(open(jpath)); m=json.load(open(os.path.join(outdir,'match.json')))
    got,cap=assign(m)
    if overrides: got.update({k:(v,9.0) for k,v in overrides.items()})
    first={}
    for s in d['steps']:
        for c in s['callouts']:
            if c['key'] is not None and c['bbox'] is not None and str(c['key']) not in first: first[str(c['key'])]=(s['page'],c['bbox'])
    invbox={e['element']:((e.get('pdf',pdf),e['page']),e['bbox']) for e in d['inventory'] if e['bbox']}
    pngs=render(pdf,[p for p,_ in first.values()]+[p for p,_ in invbox.values()],os.path.join(outdir,'pages'))
    ids=list(m['callouts']); per=6; cw=80; ch=110
    rows=(len(ids)+per-1)//per
    sheet=Image.new('RGB',(per*(2*cw+16),rows*ch),'white'); dr=ImageDraw.Draw(sheet); f=ImageFont.load_default()
    for i,o in enumerate(ids):
        r,c=divmod(i,per); x=c*(2*cw+16); y=r*ch
        n,_=norm(crop(pngs[first[o][0]],first[o][1])); sheet.paste(n.resize((cw-4,cw-4)),(x+2,y+2))
        el=got.get(o,(None,0))[0]
        if el:
            n2,_=norm(crop(pngs[invbox[el][0]],invbox[el][1],pad=2)); sheet.paste(n2.resize((cw-4,cw-4)),(x+cw+2,y+2))
        dr.text((x+2,y+cw),f"{i}: {m['callouts'][o]['qty']}x -> {el or '??'}",fill='black' if el else 'red',font=f)
        dr.text((x+2,y+cw+11),f"obj {o} s={got.get(o,(None,0))[1]:.2f}",fill='gray',font=f)
        dr.rectangle((x,y,x+2*cw+12,y+ch-2),outline='#ccc')
    sheet.save(os.path.join(outdir,'verify.png')); print(sheet.size, 'unassigned', [i for i,o in enumerate(ids) if o not in got])
if __name__=='__main__':
    ov=json.load(open(sys.argv[4])) if len(sys.argv)>4 else None
    main(sys.argv[1],sys.argv[2],sys.argv[3],ov)
