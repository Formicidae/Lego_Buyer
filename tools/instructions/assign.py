"""Capacity-constrained assignment of callout clusters to inventory elements by match score."""
import json, sys, collections
def assign(m, min_score=-9):
    inv={e['element']:e['qty'] for e in m['inventory']}
    cap=dict(inv); pairs=[]
    for objid,c in m['callouts'].items():
        for el,sc,_ in m['matches'][objid]:
            pairs.append((sc,objid,el))
    pairs.sort(reverse=True)
    got={}
    for sc,objid,el in pairs:
        if objid in got or sc<min_score: continue
        q=m['callouts'][objid]['qty']
        if cap.get(el,0)>=q:
            got[objid]=(el,sc); cap[el]-=q
    return got,cap
if __name__=='__main__':
    m=json.load(open(sys.argv[1]))
    got,cap=assign(m)
    un=[o for o in m['callouts'] if o not in got]
    print('assigned',len(got),'of',len(m['callouts']),'unassigned clusters:',[(o,m['callouts'][o]['qty'],m['matches'][o][0][0]) for o in un])
    left={e:q for e,q in cap.items() if q>0}
    print('inventory left over:',len(left),'elements,',sum(left.values()),'pieces:',left)
