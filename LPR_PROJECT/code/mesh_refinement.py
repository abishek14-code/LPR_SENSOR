"""Conforming 1-to-4 triangle refinement preserving bodies and boundary IDs."""
from pathlib import Path
import numpy as np

def refine(source,target):
    source,target=Path(source),Path(target)
    nodes=np.loadtxt(source/'mesh.nodes'); elements=np.loadtxt(source/'mesh.elements',dtype=int)
    boundaries=np.atleast_2d(np.loadtxt(source/'mesh.boundary',dtype=int))
    xyz=[list(row[2:5]) for row in nodes];mid={};triangles=[];children={}
    def midpoint(a,b):
        key=tuple(sorted((int(a),int(b))))
        if key not in mid:
            xyz.append(((np.array(xyz[a-1])+xyz[b-1])/2).tolist());mid[key]=len(xyz)
        return mid[key]
    for eid,body,typ,a,b,c in elements:
        assert typ==303
        ab,bc,ca=midpoint(a,b),midpoint(b,c),midpoint(c,a)
        children[int(eid)]=[]
        for t in [(a,ab,ca),(ab,b,bc),(ca,bc,c),(ab,bc,ca)]:
            children[int(eid)].append(len(triangles)+1);triangles.append([len(triangles)+1,body,303,*t])
    edges=[]
    for _,tag,parent,other,typ,a,b in boundaries:
        assert typ==202 and other==0
        m=midpoint(a,b)
        for u,v in [(a,m),(m,b)]:
            match=[i for i in children[int(parent)] if {u,v} <= set(triangles[i-1][3:])]
            assert len(match)==1
            edges.append([len(edges)+1,tag,match[0],0,202,u,v])
    np.savetxt(target/'mesh.nodes',[[i+1,-1,*p] for i,p in enumerate(xyz)],fmt=['%d','%d','%.16g','%.16g','%.16g'])
    np.savetxt(target/'mesh.elements',triangles,fmt='%d');np.savetxt(target/'mesh.boundary',edges,fmt='%d')
    (target/'mesh.header').write_text(f'{len(xyz)} {len(triangles)} {len(edges)}\n2\n202 {len(edges)}\n303 {len(triangles)}\n')
