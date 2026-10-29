import json
import os
from pathlib import Path
import tempfile
import unittest
from tb4.trace_bridge import Reader, append_private, event_from_message, verifier_event

class TraceBridgeTests(unittest.TestCase):
    def test_sanitized_one_event_per_completed_model_turn(self):
        msg = {'role':'assistant','model':'safe-model','timestamp':1793241971877,
               'stopReason':'toolUse','content':'secret raw answer',
               'usage':{'input':846,'output':56,'cacheRead':1510,
                        'cacheWrite':0,'totalTokens':2412,'reasoning':0}}
        event=event_from_message(msg,session_id='a'*64,ordinal=2,host='deathstar')
        self.assertEqual(event['total_tokens'],2412)
        self.assertEqual(event['cache_read'],1510)
        self.assertEqual(event['source'],'terminal-bench')
        self.assertNotIn('secret',json.dumps(event))
        self.assertEqual(event['event_id'],event_from_message(msg,session_id='a'*64,ordinal=2,host='deathstar')['event_id'])
        self.assertIsNone(event_from_message({**msg,'role':'toolResult'},session_id='a'*64,ordinal=2,host='deathstar'))
        self.assertEqual(verifier_event({'verifier_result':{'rewards':{'reward':0.0}}},session_id='b'*64,host='joyner',ts='2026-10-29T04:00:00Z')['status'],'failed')
        self.assertIsNone(verifier_event({},session_id='b'*64,host='joyner',ts='2026-10-29T04:00:00Z'))

    def test_incremental_reader_no_duplicate_and_partial_line(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'job-safe'
            trial=root/'harbor'/'job-safe'/'trial-safe';(trial/'agent').mkdir(parents=True)
            pi=trial/'agent'/'pi.txt'
            row={'type':'message_end','message':{'role':'assistant','model':'good','timestamp':1793241971877,
                'usage':{'input':1,'output':2,'totalTokens':3},'content':'do not publish'}}
            pi.write_text(json.dumps(row)+'\n'+json.dumps(row)[:18]);reader=Reader(root,'x1-370')
            self.assertEqual(len(reader.poll()),1)
            self.assertEqual(reader.poll(),[])
            with pi.open('a') as stream:stream.write(json.dumps(row)[18:]+'\n')
            self.assertEqual(len(reader.poll()),1)
            (trial/'result.json').write_text(json.dumps({'verifier_result':{'rewards':{'reward':1.0}}}))
            self.assertEqual(reader.poll()[0]['status'],'passed')
            self.assertEqual(reader.poll(),[])

    def test_private_output_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            private=Path(d)/'private';private.mkdir(mode=0o700)
            file=private/'events.jsonl';event={'schema':'tb4.telemetry.v1','event_type':'model_turn'}
            self.assertEqual(append_private(file,[event]),1)
            self.assertEqual(file.stat().st_mode & 0o777,0o600)
            link=private/'events-link';link.symlink_to(file)
            with self.assertRaises(OSError):append_private(link,[event])
            public=Path(d)/'public';public.mkdir(mode=0o755)
            with self.assertRaises(ValueError):append_private(public/'events',[event])

if __name__=='__main__':unittest.main()
