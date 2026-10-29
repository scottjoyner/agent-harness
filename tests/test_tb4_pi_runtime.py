"""Execute the generated Pi TS extension with mocked Pi events under Node 22."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from tb4.pi_agent import GuardedPi


class GuardRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is not installed')
    def test_recovery_hook_blocks_repeated_failed_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            ts = Path(tmp) / 'guard.ts'
            js = Path(tmp) / 'scenario.mjs'
            ts.write_text(GuardedPi._build_max_turns_extension(12))
            js.write_text('''import agent from './guard.ts';
const hooks = {};
agent({on(name, fn) { hooks[name] = fn; }});
const call = (id, name, input) => hooks.tool_call({toolCallId:id,toolName:name,input});
const result = (id, error, toolName='bash', input={}, content=[]) => hooks.tool_result({toolCallId:id,isError:error,toolName,input,content});
hooks.agent_start();
if (call('1','bash',{command:'invalid-command'})) throw Error('first call blocked');
result('1',true);
const blocked = call('2','bash',{command:'invalid-command'});
if (!blocked?.block || !blocked.reason.includes('identical')) throw Error('repeat not blocked');
if (call('3','bash',{command:'pwd'})) throw Error('recovery command blocked');
result('3',false);
if (call('4','bash',{command:'invalid-command'})) throw Error('state reset failed');
result('4',true);
const unsafeCat = call('6','bash',{command:'cat > /tmp/target.txt'});
if (!unsafeCat?.block || !unsafeCat.reason.includes('empty files')) throw Error('truncating cat not blocked');
const unsafeBareCat = call('7','bash',{command:'cat'});
if (!unsafeBareCat?.block) throw Error('bare cat not blocked');
const goodHereDoc = call('8','bash',{command:'cat > /tmp/target.txt <<EOF\\nhello\\nEOF'});
if (goodHereDoc?.block) throw Error('heredoc incorrectly blocked');
if (call('9','read',{path:'/app'})) throw Error('first read unexpectedly blocked');
result('9',true,'read',{path:'/app'},[{type:'text',text:'EISDIR: illegal operation on a directory, read'}]);
const knownDir = call('10','read',{path:'/app',limit:9});
if (!knownDir?.block || !knownDir.reason.includes('directory')) throw Error('known directory not blocked');
const badDir = call('5','read',{path:'/tmp/a/'});
if (!badDir?.block || !badDir.reason.includes('directory')) throw Error('directory read not blocked');
console.log(JSON.stringify({repeat_blocked:blocked.block,directory_read_blocked:badDir.block,normal_recovery_allowed:true}));
''')
            proc = subprocess.run(['node', '--experimental-strip-types', str(js)],
                                  text=True, capture_output=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)['repeat_blocked'])


if __name__ == '__main__':
    unittest.main()
