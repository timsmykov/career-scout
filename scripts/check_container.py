"""Offline Docker acceptance: actual container recreation, SQLite and token persistence.

Creates an isolated synthetic volume; never opens the production volume or job APIs.
"""

import argparse
import subprocess
import uuid

FIRST = r"""
import json
from pathlib import Path
import httpx
from career_scout.hh import Credentials, HHClient, normalize
from career_scout.storage import Store, SCHEMA
from career_scout.collector import collect
from career_scout.search_config import load_config, plan

p=Path('/tmp/test.env')
p.write_text('HH_USER_AGENT=OfflineContainerTest\nHH_CLIENT_ID=test-app\nHH_ACCESS_TOKEN=initial-test\n')
c=Credentials(p, token_path=Path('/data/auth/token.json'))
phase=__import__('sys').argv[1]
if phase=='first':
    assert not Path('/data/ledger.sqlite3').exists()
    c.save_token('persisted-test')
else:
    assert c.values['HH_ACCESS_TOKEN']=='persisted-test'
if phase=='first':
    import sqlite3
    original=normalize({'id':'0','name':'Legacy','description':'<p>Legacy text</p>'},'0')
    with sqlite3.connect('/data/ledger.sqlite3') as old:
        old.executescript(SCHEMA+'\nPRAGMA user_version=1;')
        old.execute('INSERT INTO vacancies VALUES(?,?,?,?,?,?)',
          ('0','{}','legacy','full',original['content_hash'],original['detail_fetched_at']))
        old.execute('INSERT INTO snapshots VALUES(?,?,?,?)',
          ('0',original['content_hash'],original['detail_fetched_at'],json.dumps(original)))
s=Store(Path('/data/ledger.sqlite3'))
assert s.db.execute('PRAGMA user_version').fetchone()[0]==4
assert s.inspect('0')['snapshots'][0]['description']=='Legacy text'
assert s.inspect('0')['normalized_snapshots'][0]['description']=='Legacy text'
committed=(set(json.loads(Path('/data/first-full-ids.json').read_text()))
           if phase!='first' else set())

def handler(req):
    if req.url.path=='/vacancies':
        page=int(req.url.params['page'])
        return httpx.Response(200,json={'items':[{'id':str(i),'name':'Synthetic role'}
          for i in range(page*100+1,(page+1)*100+1)],'page':page,
          'pages':3,'per_page':100,'found':300})
    vid=req.url.path.split('/')[-1]
    assert vid not in committed, 'committed detail was fetched twice'
    return httpx.Response(200,json={'id':vid,
      'name':('Senior' if int(vid)%3==0 else 'Junior')+' analyst',
      'experience':{'id':'between1And3'}, 'salary':None,
      'description':'<p>Complete synthetic IT text '+vid+'</p>'})

if phase=='first':
    config=load_config(Path('/app/configs/search.toml'))
    frozen=plan(config,queries=['test'])
    rid=s.create(frozen['queries'],frozen['start'],frozen['end'],100,24,plan_snapshot=frozen)
    Path('/data/run-id').write_text(rid)
else:
    rid=Path('/data/run-id').read_text()
transport=httpx.Client(transport=httpx.MockTransport(handler))
client=HHClient(c,client=transport,max_requests=21 if phase=='first' else 400,interval=0)
report=collect(s,client,rid,max_details=400)
assert report['full_details']==(20 if phase=='first' else 300), report
assert report['status']==('partial' if phase=='first' else 'complete'), report
if phase=='first':
    Path('/data/first-full-ids.json').write_text(json.dumps([x['source_id'] for x in s.ready(rid)]))
else:
    assert report['requests']==303, report
    exported=s.export(rid)
    assert exported['schema_version']==2
    assert len(exported['vacancies'])==200
    assert len({x['source_id'] for x in exported['vacancies']})==200
    assert len(s.export(rid,include_excluded=True)['vacancies'])==300
    assert report['filtering']['KEEP']==200
    assert report['filtering']['FILTERED_OUT']==100
    assert report['filtering']['pending']==0
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()
print(json.dumps({'phase':phase,'full':report['full_details'],'status':report['status']}))
s.close()
transport.close()
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="career-scout:local")
    args = parser.parse_args()
    volume = "career-scout-test-" + uuid.uuid4().hex

    def command(*parts):
        return subprocess.run(parts, check=True, capture_output=True, text=True).stdout.strip()

    command("docker", "volume", "create", volume)
    try:
        for phase in ("first", "recreated"):
            result = command(
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:mode=1777,size=32m",
                "--mount",
                f"type=volume,source={volume},target=/data",
                "--entrypoint",
                "/app/.venv/bin/python",
                args.image,
                "-c",
                FIRST,
                phase,
            )
            print(result)
    finally:
        # Only this unique volume with synthetic data; never the real career-scout-data.
        command("docker", "volume", "rm", volume)


if __name__ == "__main__":
    main()
