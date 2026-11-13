"""Offline Docker acceptance: actual container recreation, SQLite and token persistence.

Creates an isolated synthetic volume; never opens the production volume or job APIs.
"""

import argparse
import json
import subprocess
import uuid

FIRST = r"""
import json
from pathlib import Path
from datetime import UTC, datetime, timedelta
import httpx
from career_scout.hh import Credentials, HHClient, normalize
from career_scout.storage import Store, SCHEMA
from career_scout.collector import collect
from career_scout.search_config import load_config, plan

p=Path('/tmp/test.env')
p.write_text('HH_USER_AGENT=OfflineContainerTest\nHH_CLIENT_ID=test-app\nHH_ACCESS_TOKEN=initial-test\n')
c=Credentials(p, token_path=Path('/data/auth/token.json'))
phase=__import__('sys').argv[1]
publication=(datetime.now(UTC)-timedelta(days=1)).isoformat()
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
assert s.db.execute('PRAGMA user_version').fetchone()[0]==5
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
      'published_at':publication,
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

CAMPAIGN = r"""
import json, sys
from pathlib import Path
from datetime import UTC, datetime, timedelta
import httpx
from career_scout.campaigns import Campaigns
from career_scout.hh import HHClient, Credentials
from career_scout.search_config import load_config
from career_scout.storage import Store
s=Store(Path('/data/ledger.sqlite3'))
c=Campaigns(s)
phase=sys.argv[1]
publication=(datetime.now(UTC)-timedelta(days=1)).isoformat()
ids=list(range(1,301))
details=[]
def handler(req):
    if req.url.path=='/vacancies':
        assert 'date_from' in req.url.params
        p=int(req.url.params['page'])
        return httpx.Response(200,json={
          'items':[{'id':str(i),'name':'Changed listing'} for i in ids[p*100:(p+1)*100]],
          'page':p,'pages':(len(ids)+99)//100,'found':len(ids),'per_page':100})
    vid=req.url.path.split('/')[-1]
    assert int(vid)>300, 'old complete description was fetched twice'
    details.append(vid)
    return httpx.Response(200,json={'id':vid,'name':'Junior IT analyst',
      'published_at':publication,
      'description':'<p>Full synthetic description.</p>'})
def factory(frozen,budget):
    return HHClient(Credentials(Path('/tmp/test.env')),
      client=httpx.Client(transport=httpx.MockTransport(handler)),
      max_requests=budget,interval=0)
Path('/tmp/test.env').write_text('HH_USER_AGENT=OfflineCampaignTest\nHH_ACCESS_TOKEN=fake\n')
if phase=='campaign-first':
    cid=c.create(load_config(Path('/app/configs/search.toml')),'initial',
                 sources=['hh'],queries=['test'])
    Path('/data/campaign-id').write_text(cid)
    result=c.drive(cid,factory,chunk_requests=1,max_rounds=1)
    assert result['status']=='partial'
    assert not details
else:
    cid=Path('/data/campaign-id').read_text()
    assert c.drive(cid,factory)['status']=='complete'
    assert c.report(cid)['totals']['known_ids']==300
    assert c.report(cid)['totals']['new_descriptions']==0
    assert len(c.export(cid,include_excluded=True)['vacancies'])==300
    ids.extend([301,302])
    daily=c.create(load_config(Path('/app/configs/search.toml')),'new',
                   sources=['hh'],queries=['test'])
    assert c.drive(daily,factory,chunk_requests=2)['status']=='complete'
    delta=c.export(daily)
    assert {v['source_id'] for v in delta['vacancies']}=={'301','302'}
    assert len(details)==2
    details.clear()
    again=c.create(load_config(Path('/app/configs/search.toml')),'new',
                   sources=['hh'],queries=['test'])
    assert c.drive(again,factory)['status']=='complete'
    assert c.export(again)['vacancies']==[]
    assert not details
assert not s.db.execute('PRAGMA foreign_key_check').fetchall()
print(json.dumps({'phase':phase,'campaign':cid,'detail_requests':len(details)}))
s.close()
"""


CONTEXT = r"""
import json,sys
from pathlib import Path
from datetime import UTC,datetime
from contextlib import closing
from career_scout.agent_state import AgentState
from career_scout.agent_service import context_create
from career_scout import context_engine
from career_scout.context_models import EvaluationConnection
from career_scout.analysis_store import AnalysisStore
from career_scout.batch_worker import BatchReply
from career_scout.codex_worker import WorkerReply
from career_scout.context_policy import ContextResult
from career_scout.hh import normalize
from career_scout.normalization import project_snapshot
root=Path('/data/agent')
phase=sys.argv[1]
if phase=='agent-first':
    state=AgentState(root,write=True)
    ctx=context_create(state,{'resumes':[{'id':'clinical','title':'Synthetic clinician',
      'text':'Лечил пациентов.'}]},'ctx')['result']
    raw={'id':'fake','name':'Врач','description':'<p>Лечить пациентов.</p>',
         'published_at':datetime.now(UTC).isoformat()}
    cfg=EvaluationConnection(external_calls_enabled=True)
    run=context_engine.prepare(state,ctx['context_id'],None,
         [project_snapshot(normalize(raw,'fake'))],cfg.model_dump(mode='json'),10,1,0)
    class Worker:
        def __init__(self,config): pass
        def cancel(self): pass
        def batch(self,requests):
            replies={}
            for r in requests:
                value=ContextResult(schema_version=4,policy='context-1',
                 evaluation_key=r.evaluation_key,profile_id=r.profile_id,verdict='PASS',
                 reason='Synthetic match',assessments=[dict(kind='responsibility',
                 requirement='Лечить пациентов',mandatory=True,critical=True,
                 vacancy_quote='Лечить пациентов.',resume_quote='Лечил пациентов.',
                 status='confirmed',explanation='Synthetic evidence')],
                 preference_checks=[],uncertainties=[],questions=[])
                replies[r.evaluation_key]=WorkerReply(value.model_dump(mode='json'))
            return BatchReply(replies,{},None,{})
    with closing(AnalysisStore(Path(run['database']))) as store:
        assert context_engine.execute(store,run['run_id'],worker_factory=Worker) is None
    (root/'run.json').write_text(json.dumps(run))
    state.close()
else:
    run=json.loads((root/'run.json').read_text())
    result=context_engine.status(run['database'],run['run_id'])
    assert result['status']=='complete' and result['outcomes']=={'ACCEPTED':1}
    assert len(context_engine.recommendations(run['database']))==1
print(json.dumps({'phase':phase,'context_analysis':'persisted','real_model_calls':0}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="career-scout:local")
    args = parser.parse_args()
    volume = "career-scout-test-" + uuid.uuid4().hex

    def command(*parts):
        result = subprocess.run(parts, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(
                f"Container acceptance exited {result.returncode}:\n"
                + (result.stderr or result.stdout)
            )
        return result.stdout.strip()

    command("docker", "volume", "create", volume)
    try:
        schema = json.loads(
            command(
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--entrypoint",
                "/app/.venv/bin/career-scout",
                args.image,
                "schema",
                "--name",
                "context",
            )
        )
        assert schema["status"] == "ok" and schema["result"]["capabilities"]["context_versions"]
        print("Agent CLI executable/schema OK; network disabled.")
        for phase in (
            "first",
            "recreated",
            "campaign-first",
            "campaign-recreated",
            "agent-first",
            "agent-recreated",
        ):
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
                CONTEXT
                if phase.startswith("agent")
                else CAMPAIGN
                if phase.startswith("campaign")
                else FIRST,
                phase,
            )
            print(result)
    finally:
        # Only this unique volume with synthetic data; never the real career-scout-data.
        command("docker", "volume", "rm", volume)


if __name__ == "__main__":
    main()
