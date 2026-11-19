#!/usr/bin/env python3
"""Verify retained scientific source, delivery hashes and offline report links."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import hashlib, json, sys

ROOT=Path(__file__).resolve().parent

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()

class Links(HTMLParser):
    def __init__(self):super().__init__();self.targets=[]
    def handle_starttag(self,tag,attrs):
        for key,value in attrs:
            if value and key in {'href','src'}:self.targets.append((tag,key,value))

def main():
    manifest=json.loads((ROOT/'PACKAGE_SHA256.json').read_text())
    for name,expected in manifest.items():
        path=ROOT/name
        if not path.is_file() or sha(path)!=expected:
            raise RuntimeError('Delivery hash mismatch: '+name)
    originals=json.loads((ROOT/'evidence/ORIGINAL_PROJECT_HASHES.json').read_text())
    for name,expected in originals.items():
        if sha(ROOT/'project'/name)!=expected:raise RuntimeError('Scientific source mismatch: '+name)
    summary=json.loads((ROOT/'VALIDATION_SUMMARY.json').read_text())
    assert summary['counts']=={'ACCEPT':9,'REJECT':6,'WAIT_OR_REPEAT':1}
    assert summary['native_executions']==22 and summary['P3_changes'] is False
    reports=[ROOT/'START_HERE.html',ROOT/'decision_report.html',ROOT/'index.html']
    reports.extend((ROOT/'evidence/native/20261021T133828').rglob('*.html'))
    checked=0
    for report in reports:
        parser=Links();parser.feed(report.read_text())
        for tag,key,target in parser.targets:
            url=urlsplit(target)
            if url.scheme or url.netloc:
                if key=='src' and url.scheme in {'http','https'}:
                    raise RuntimeError('External resource in offline report: '+str(report))
                continue
            if url.path and not (report.parent/unquote(url.path)).exists():
                raise RuntimeError('Broken report link: '+str(report)+' -> '+target)
            checked+=1
    print(json.dumps(dict(status='PASS',payload_hashes=len(manifest),unchanged_scientific_files=len(originals),
        html_reports=len(reports),local_links_checked=checked,decisions=summary['counts'],
        note='Static verification; no browser visual QA or new native simulation performed.'),indent=2))

if __name__=='__main__':main()
