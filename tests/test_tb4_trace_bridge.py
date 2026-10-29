import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tb4.trace_bridge import Reader, append_private, event_from_message, verifier_event

class TraceBridgeTests(unittest.TestCase):
    def test_sanitized_one_event_per_completed_model_turn(self):
        msg = {'role':'assistant','model':'safe-model','timestamp':1793241971877,
               'stopReason':'toolUse','content':'secret raw answer',
               'usage':{'input':846,'output':56,'cacheRead':1510,
                        'cacheWrite':0,'totalTokens':2412,'reasoning':0}}
        event=event_from_message(msg,session_id='a'*64,ordinal=2,host='deathstar')
        self.assertEqual(event['total_tokens'],2412)
        self.assertEqual(event['total_origin'],'reported')
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

    def test_publisher_heartbeats_mark_active_and_stopped_without_model_turns(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'heartbeat-only'
            root.mkdir(mode=0o700)
            private=Path(d)/'private'
            private.mkdir(mode=0o700)
            journal=private/'events.jsonl'
            reader=Reader(root,'deathstar')
            self.assertEqual(reader.export_to(journal,publisher_state='active'),1)
            self.assertEqual(reader.export_to(journal,publisher_state='active'),0)
            self.assertEqual(reader.heartbeat_sequence,1)
            reader.last_heartbeat_monotonic-=31
            self.assertEqual(reader.export_to(journal,publisher_state='active'),1)
            self.assertEqual(reader.export_to(journal,publisher_state='stopped'),1)
            events=[json.loads(x) for x in journal.read_text().splitlines()]
            self.assertEqual([x['status'] for x in events],['active','active','stopped'])
            self.assertTrue(all(x['event_type']=='publisher_state' for x in events))
            self.assertEqual(len({x['event_id'] for x in events}),3)
            self.assertTrue(all(x['published_utc'] for x in events))
            self.assertNotIn(str(root),journal.read_text())

    def test_publisher_state_append_failure_restores_retry_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'heartbeat'
            root.mkdir(mode=0o700)
            private=Path(d)/'private'
            private.mkdir(mode=0o700)
            journal=private/'events.jsonl'
            reader=Reader(root,'deathstar')
            with patch('tb4.trace_bridge.append_private',side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError,'disk full'):
                    reader.export_to(journal,publisher_state='active')
            self.assertEqual(reader.heartbeat_sequence,0)
            self.assertIsNone(reader.last_heartbeat_monotonic)
            self.assertEqual(reader.export_to(journal,publisher_state='active'),1)
            records=[json.loads(x) for x in journal.read_text().splitlines()]
            self.assertEqual(records[0]['event_id'],__import__('tb4.trace_bridge',fromlist=['digest']).digest('publisher-state',root.name,1))
            self.assertEqual(reader.export_to(journal,publisher_state='stopped'),1)

    def test_failed_journal_append_retries_same_event_and_verifier_ids(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'job-safe'
            trial=root/'harbor'/'job-safe'/'trial-safe'
            (trial/'agent').mkdir(parents=True)
            message={'type':'message_end','message':{'role':'assistant',
                'model':'good','timestamp':1793241971877,
                'usage':{'input':1,'output':2,'totalTokens':3},
                'content':'PRIVATE BENCHMARK PROMPT NOT EXPORTED'}}
            (trial/'agent'/'pi.txt').write_text(json.dumps(message)+'\n')
            (trial/'result.json').write_text(json.dumps({'verifier_result':
                {'rewards':{'reward':0.0}}}))
            out=Path(d)/'private'/'events.jsonl'
            out.parent.mkdir(mode=0o700)
            reader=Reader(root,'deathstar')
            with patch('tb4.trace_bridge.append_private',side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError,'disk full'):
                    reader.export_to(out)
            self.assertEqual(reader.state,{})
            self.assertEqual(reader.verifiers,set())
            self.assertEqual(reader.export_to(out),2)
            rows=[json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual([e['event_type'] for e in rows],
                             ['model_turn','trial_verified'])
            self.assertEqual(reader.export_to(out),0)
            self.assertEqual(len(out.read_text().splitlines()),2)
            self.assertNotIn('PRIVATE BENCHMARK PROMPT',out.read_text())
            self.assertEqual(rows[-1]['official_reward'],0.0)
            self.assertRegex(rows[0]['published_utc'],r'^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
            self.assertEqual(rows[0]['published_utc'],rows[-1]['published_utc'])

    def test_missing_total_is_marked_derived_and_does_not_double_count_cache(self):
        message={'role':'assistant','model':'deathstar-ling-rx480',
                 'timestamp':1793250191278,
                 'usage':{'input':100,'output':40,'cacheRead':75,'cacheWrite':9,
                          'reasoning':8}}
        record=event_from_message(message,session_id='a'*64,ordinal=1,host='deathstar')
        self.assertEqual(record['total_tokens'],140)
        self.assertEqual(record['total_origin'],'derived')
        self.assertEqual(record['cache_read'],75)
        self.assertEqual(record['reasoning_tokens'],8)
        message['usage']={'cacheRead':32,'cacheWrite':5}
        record=event_from_message(message,session_id='a'*64,ordinal=2,host='deathstar')
        self.assertEqual(record['total_tokens'],37)
        self.assertEqual(record['total_origin'],'derived')

    def test_publisher_receipt_replaces_caller_supplied_timestamp(self):
        with tempfile.TemporaryDirectory() as root:
            journal=Path(root)/'journal'/'events.jsonl'
            journal.parent.mkdir(mode=0o700)
            input_event={'schema':'tb4.telemetry.v1','event_type':'model_turn',
                         'event_id':'e'*64,'published_utc':'2000-01-01T00:00:00Z'}
            self.assertEqual(append_private(journal,[input_event]),1)
            saved=json.loads(journal.read_text().splitlines()[0])
            self.assertNotEqual(saved['published_utc'],input_event['published_utc'])
            self.assertEqual(input_event['published_utc'],'2000-01-01T00:00:00Z')

    def test_partial_disk_write_is_rolled_back_before_retry(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'private'/'events.jsonl'
            path.parent.mkdir(mode=0o700)
            event={'schema':'tb4.telemetry.v1','event_type':'model_turn',
                   'event_id':'e'*64}
            self.assertEqual(append_private(path,[event]),1)
            prior=path.read_bytes()
            original_write=os.write
            calls=[0]
            def partial_then_error(fd,data):
                calls[0]+=1
                if calls[0]==1:
                    return original_write(fd,data[:max(1,len(data)//2)])
                raise OSError('ENOSPC synthetic')
            with patch('tb4.trace_bridge.os.write',side_effect=partial_then_error):
                with self.assertRaisesRegex(OSError,'ENOSPC'):
                    append_private(path,[event])
            self.assertEqual(path.read_bytes(),prior)
            self.assertEqual(append_private(path,[event]),1)
            self.assertEqual(len(path.read_text().splitlines()),2)

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
