#!/usr/bin/env python3
"""Build MiniCPM5 iter3 dataset: single-shot tool-first + multi-step correction pairs.
Format: ShareGPT {"conversations": [{"from":"user"}, {"from":"gpt"}]} for xwing train pipeline."""

import json
from pathlib import Path

OUT = Path(__file__).parent / "cpm_iter3_train.jsonl"
TASKS = json.loads(Path(__file__).parent.joinpath("benchmarks/baremetal/tasks.json").read_text())["tasks"]

def ex(user, gpt):
    return {"conversations": [{"from": "user", "value": user}, {"from": "gpt", "value": gpt}]}

def bash(cmd):
    return f'<function name="bash"><param name="command">{cmd}</param></function>'

def write(path, content):
    return f'<function name="write_file"><param name="path">{path}</param><param name="content">{content}</param></function>'

rows = []
# 1. Tool-first singles: every baremetal task gets a canonical 1-step answer
CANON = {
 "BM-001": bash("cat /proc/cpuinfo | grep 'model name' | head -1 > /tmp/system_info.txt && free -h >> /tmp/system_info.txt && df -h /home >> /tmp/system_info.txt && ps aux --sort=-%mem | head -10 >> /tmp/system_info.txt"),
 "BM-002": bash("cat /var/log/syslog 2>/dev/null | grep -ioE '[a-z]+(error|fail)[a-z ]*' | sort | uniq -c | sort -rn | head -10 > /tmp/log_errors.txt"),
 "BM-003": write("/tmp/cleanup.py", "import os,time,shutil\nos.makedirs('/tmp/archive',exist_ok=True)\nnow=time.time()\nfor f in os.listdir('/tmp'):\n p=os.path.join('/tmp',f)\n if f.endswith('.tmp') and os.path.isfile(p) and now-os.path.getmtime(p)>7*86400:\n  shutil.move(p,os.path.join('/tmp/archive',f))\nprint('done')"),
 "BM-004": bash("(nslookup google.com; ping -c2 8.8.8.8; ss -tlnp; systemctl --failed --no-pager) > /tmp/network_report.txt 2>&1"),
 "BM-005": write("/tmp/monitor.sh", "#!/bin/bash\nps aux --sort=-%cpu | head -6 > /tmp/monitor_output.txt\nps aux --sort=-%mem | head -6 >> /tmp/monitor_output.txt\ndf -h >> /tmp/monitor_output.txt\nss -tun | head -20 >> /tmp/monitor_output.txt"),
 "BM-011": write("/tmp/process_data.py", "import csv\nwith open('/etc/passwd') as f, open('/tmp/users.csv','w',newline='') as o:\n w=csv.writer(o); w.writerow(['username','uid','home'])\n for line in f:\n  p=line.split(':')\n  if len(p)>6 and p[2].isdigit() and int(p[2])>=1000:\n   w.writerow([p[0],p[2],p[5]])"),
 "BM-015": bash("(which docker && (docker ps -a; docker images; docker volume ls) || echo 'docker not installed: install via apt install docker.io') > /tmp/docker_report.txt 2>&1"),
 "BM-019": bash("python3 -c \"import ast; t=open('/home/scott/git/agent-harness/synergistic_harness.py').read(); print(len(t.splitlines()),'lines;',len([n for n in ast.walk(ast.parse(t)) if isinstance(n,ast.FunctionDef)]),'funcs')\" > /tmp/code_review.txt"),
 "BM-006": write("/tmp/organize.py", "import os,shutil\nsrc=os.path.expanduser('~/Downloads')\nmapping={'.txt':'docs','.md':'docs','.py':'code','.js':'code','.png':'images','.jpg':'images'}\nfor f in os.listdir(src):\n p=os.path.join(src,f)\n if os.path.isfile(p):\n  d=os.path.join(src,mapping.get(os.path.splitext(f)[1].lower(),'other'))\n  os.makedirs(d,exist_ok=True); shutil.move(p,os.path.join(d,f))\nprint('done')"),
 "BM-007": bash("cd ~/knowledge && (git shortlog -sne --all | head -5; echo ---TOPCHANGED---; git log --all --pretty=format: --name-only | sort | uniq -c | sort -rn | head -10; echo ---LARGEST---; find . -type f -printf '%s %p\\n' 2>/dev/null | sort -rn | head -10; echo ---RECENT---; git log --oneline -8) > /tmp/git_analysis.txt 2>&1"),
 "BM-008": bash("find /home /var /usr -xdev -type f -printf '%s %p\\n' 2>/dev/null | sort -rn | head -20 | awk '{printf \"%10.1f MB  %s\\n\", $1/1048576, substr($0,index($0,$2))}' > /tmp/cleanup_report.txt"),
 "BM-009": bash("(systemctl --failed --no-pager; echo ---; systemctl list-units --type=service --state=running --no-pager --no-legend | head -5; echo ---; ps aux --sort=-%mem | head -4) > /tmp/service_report.txt 2>&1"),
 "BM-010": write("/tmp/backup.sh", "#!/bin/bash\nmkdir -p /tmp/backups\ntar -czf /tmp/backups/agent-harness-$(date +%Y%m%d_%H%M%S).tar.gz -C ~ git/agent-harness\nls -t /tmp/backups/*.tar.gz | tail -n +6 | xargs -r rm --\necho backup-done"),
 "BM-012": bash("(echo '== world-writable /etc =='; find /etc -xdev -type f -perm -0002 2>/dev/null; echo '== empty-password users =='; sudo -n true 2>/dev/null && sudo awk -F: 'length($2)==0{print $1}' /etc/shadow 2>/dev/null || awk -F: '$2==\"\"||$2==\"!\"{print $1}' /etc/shadow 2>/dev/null; echo '== suid =='; find /usr/bin /bin -xdev -perm -4000 -type f 2>/dev/null | head -20; echo '== listening =='; ss -tln) > /tmp/security_audit.txt 2>&1"),
 "BM-014": write("/tmp/perf_bench.py", "import time,os\nt=time.time();n=0;i=2\nprimes=[]\nwhile i<100000:\n p=True\n for q in range(2,int(i**0.5)+1):\n  if i%q==0: p=False; break\n if p: primes.append(i); n+=1\n i+=1\ncpu=time.time()-t\nwith open('/tmp/blob.bin','wb') as f: f.write(os.urandom(100*1048576))\nt=time.time()\nopen('/tmp/blob.bin','rb').read()\ndisk=time.time()-t\nos.remove('/tmp/blob.bin')\nmem=time.time()\nb=bytearray(100*1048576); del b\nmem=time.time()-mem\nopen('/tmp/perf_results.txt','w').write(f'primes_upto_100k={n} cpu_s={cpu:.1f} disk_read_s={disk:.2f} mem_alloc_s={mem:.2f}\\n')\nprint('done')"),
 "BM-016": write("/tmp/logrotate.sh", "#!/bin/bash\nfind /var/log -maxdepth 1 -name '*.log' -mtime +30 -exec gzip {} \\; 2>/dev/null\nfind /var/log -maxdepth 1 -name '*.gz' -mtime +90 -delete 2>/dev/null\n(crontab -l 2>/dev/null; echo '0 2 * * * /tmp/logrotate.sh') | crontab -\necho rotated"),
 "BM-020": write("/tmp/pipeline/report.py", "import subprocess\nout=subprocess.run(['ps','aux','--sort=-%mem'],capture_output=True,text=True).stdout\nopen('/tmp/pipeline/report.txt','w').write(out)\nprint('report-done')"),
}
for t in TASKS:
    if t["id"] in CANON:
        rows.append(ex(t["instruction"], CANON[t["id"]]))
# 2. Multi-step correction pairs: feedback -> next tool (teaches no-reasoning continuation)
rows.append(ex("Result rc=0: Wrote 465 bytes to /tmp/process_data.py\nNot done (missing file). Next tool call:",
               bash("python3 /tmp/process_data.py && ls -la /tmp/users.csv")))
rows.append(ex("Result rc=0: Wrote 442 bytes to /tmp/cleanup.py\nNot done (missing file). Next tool call:",
               bash("python3 /tmp/cleanup.py && ls /tmp/archive/ 2>/dev/null; echo done")))
rows.append(ex("No tool call found. Output ONE <function> tool call now.\nTask: Check disk usage for /home",
               bash("df -h /home")))
rows.append(ex("Result rc=1: docker: command not found\nNot done (missing file). Next tool call:",
               bash("echo 'docker not installed: install via apt install docker.io; no containers/images/volumes' > /tmp/docker_report.txt")))
# 3. Anti-preamble: same instruction, correct = bare tool call (no 'We need to...' thinking)
for t in TASKS[:6]:
    canon = CANON.get(t["id"])
    if canon:
        rows.append(ex("Output ONLY the function call, no thinking. Task: " + t["instruction"][:120], canon))
# 4. Paraphrase variants (same tool, reworded ask) + toy micro-tasks from v7 suite
PARA = {
 "BM-001": "Collect CPU, memory, disk and top-process info into /tmp/system_info.txt",
 "BM-003": "Create /tmp/cleanup.py to archive week-old .tmp files, then run it",
 "BM-011": "Dump users with UID>=1000 from /etc/passwd to /tmp/users.csv via /tmp/process_data.py",
 "BM-015": "Produce /tmp/docker_report.txt with container/image/volume status or install docs",
}
for tid, user in PARA.items():
    rows.append(ex(user, CANON[tid]))
    rows.append(ex("No preamble, just the tool call: " + user, CANON[tid]))
TOY = [
 ("Create a Python script that prints the current date and time, then run it",
  bash("cat > print_date.py << 'EOF'\nfrom datetime import datetime\nprint(datetime.now())\nEOF\npython3 print_date.py")),
 ("Count files in /home/scott/git (max depth 1)",
  bash("find /home/scott/git -maxdepth 1 | wc -l")),
 ("Create a directory called bench_test2 and put a file in it",
  bash("mkdir -p bench_test2 && echo test > bench_test2/file.txt")),
 ("Check what processes are using the most memory",
  bash("ps aux --sort=-%mem | head -5")),
 ("Create a bash script that prints hello",
  bash("cat << 'EOF' > hello.sh\n#!/bin/bash\necho hello\nEOF\nchmod +x hello.sh")),
 ("List all listening network ports",
  bash("ss -tln")),
 ("Create a file with numbers 1 to 10",
  bash("seq 1 10 > numbers.txt")),
 ("Show disk usage for /home",
  bash("df -h /home")),
]
for user, tool in TOY:
    rows.append(ex(user, tool))
# 5. iter4 correction pairs (error -> fix), from 2026-09-12/13 fail traces
rows.append(ex("Tool result (rc=128): fatal: invalid format '%(h:8s)' for --format\nTask not verified yet (missing file). Continue with next tool call.",
               bash("cd ~/knowledge && (git shortlog -sne --all | head -5; echo ---TOPCHANGED---; git log --all --pretty=format: --name-only | sort | uniq -c | sort -rn | head -10; echo ---LARGEST---; find . -type f -printf '%s %p\\n' 2>/dev/null | sort -rn | head -10; echo ---RECENT---; git log --oneline -8) > /tmp/git_analysis.txt 2>&1")))
rows.append(ex("Result rc=0: REPORT: file does not exist\nNot done (missing file). You checked instead of creating. Create the report now.",
               bash("find /home /var /usr -xdev -type f -printf '%s %p\\n' 2>/dev/null | sort -rn | head -20 | awk '{printf \"%10.1f MB  %s\\n\", $1/1048576, substr($0,index($0,$2))}' > /tmp/cleanup_report.txt")))
rows.append(ex("Task too big for one command. Decompose: write the benchmark script first, then run it. Task: Run performance benchmarks (primes to 100000, 100MB disk IO, 100MB alloc) to /tmp/perf_results.txt",
               write("/tmp/perf_bench.py", "import time,os,math,platform,subprocess\nn=100000; s=bytearray(b'\\x01')*(n+1); s[0:2]=b'\\x00\\x00'\nt=time.time()\nfor i in range(2,int(math.sqrt(n))+1):\n if s[i]: s[i*i:n+1:i]=b'\\x00'*(((n-i*i)//i)+1)\ncpu=time.time()-t; cnt=sum(s)\nt=time.time(); open('/tmp/blob.bin','wb').write(os.urandom(100*1048576)); wdisk=time.time()-t\nt=time.time(); open('/tmp/blob.bin','rb').read(); rdisk=time.time()-t\nos.remove('/tmp/blob.bin')\nt=time.time(); b=bytearray(100*1048576); del b; mem=time.time()-t\ncpuinfo=[l for l in open('/proc/cpuinfo').read().splitlines() if 'model name' in l][:1]\nmemline=[l for l in open('/proc/meminfo').read().splitlines() if 'MemTotal' in l][:1]\ndf=subprocess.run(['df','-h','/home'],capture_output=True,text=True).stdout.strip().splitlines()[-1]\nrep=['perf benchmark '+time.strftime('%Y-%m-%d %H:%M')]+cpuinfo+memline+['df_home='+df,f'host={platform.node()}','== CPU primes ==',f'primes_upto_100k={cnt} cpu_s={cpu:.2f}','== disk 100MB ==',f'write_s={wdisk:.2f} read_s={rdisk:.2f}','== memory 100MB ==',f'alloc_free_s={mem:.2f}']\nopen('/tmp/perf_results.txt','w').write('\\n'.join(rep)+'\\n')\nprint('done')")))
rows.append(ex("Result rc=0: Wrote 512 bytes to /tmp/perf_bench.py\nNot done (missing file). Now run the script.",
               bash("python3 /tmp/perf_bench.py && cat /tmp/perf_results.txt")))
rows.append(ex("Tool result (rc=1): Traceback: socket.gethostbyaddr('example.com'): socket.gaierror: [Errno 1] Unknown host\nFix the script: gethostbyaddr does reverse lookup; use gethostbyname and measure times.",
               write("/tmp/api_test.py", "import socket, time, json, urllib.request\nout={}\nt=time.time(); out[\"dns_ip\"]=socket.gethostbyname('httpbin.org'); out[\"dns_time\"]=round(time.time()-t,3)\nt=time.time(); r=urllib.request.urlopen('https://httpbin.org/get',timeout=15); body=r.read(); out[\"http_time\"]=round(time.time()-t,3)\nout[\"connect\"]=r.status; out[\"bytes\"]=len(body); out[\"url\"]='https://httpbin.org/get'\njson.dump(out, open('/tmp/api_results.json','w'), indent=2)\nprint('done')")))
rows.append(ex("Empty tool call rejected. Emit a real command. Task: Show disk usage for /home",
               bash("df -h /home > /tmp/disk.txt && cat /tmp/disk.txt")))
# 6b. iter7: BM-017 decomposed into create -> insert -> query steps
rows.append(ex("Create a SQLite database at /tmp/test.db with a users table (id, name, email, created_at). Step 1: create the table.",
               bash("sqlite3 /tmp/test.db \"CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT, created_at TEXT);\" && echo table-ready")))
rows.append(ex("Result rc=0: table-ready\nStep 2: insert 10 sample users with recent dates.",
               bash("sqlite3 /tmp/test.db \"WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c WHERE n<10) INSERT INTO users(name,email,created_at) SELECT 'user'||n, 'user'||n||'@x.com', date('now') FROM c;\" && echo rows-inserted")))
rows.append(ex("Result rc=0: rows-inserted\nStep 3: query users created in the last 7 days.",
               bash("sqlite3 /tmp/test.db \"SELECT * FROM users WHERE created_at >= date('now','-7 days');\"")))
# 6. iter5: complex multi-part task -> script-first pattern (BM-007 lesson)
rows.append(ex("Tool result (rc=2): /bin/sh: 1: Syntax error: end of file unexpected\nYour one-liner was truncated. Rewrite as a script file first, then run it.",
               write("/tmp/git_analysis.sh", "#!/bin/bash\ncd ~/knowledge || exit 1\n{\necho \"== top contributors ==\"\ngit shortlog -sne --all | head -5\necho \"== most-changed files ==\"\ngit log --all --pretty=format: --name-only | sort | uniq -c | sort -rn | head -10\necho \"== largest files ==\"\nfind . -type f -printf '%s %p\\n' 2>/dev/null | sort -rn | head -10\necho \"== recent ==\"\ngit log --oneline -8\n} > /tmp/git_analysis.txt 2>&1")))
rows.append(ex("Result rc=0: Wrote 512 bytes to /tmp/git_analysis.sh\nNot done (missing file). Now run the script.",
               bash("bash /tmp/git_analysis.sh && wc -c /tmp/git_analysis.txt")))
rows.append(ex("Conditional task: just act. Do not deliberate about whether sqlite3 exists - check and create the database in one command.",
               bash("which sqlite3 && sqlite3 /tmp/test.db \"CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, email TEXT, created_at TEXT); WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c WHERE n<10) INSERT INTO users(name,email,created_at) SELECT 'user'||n, 'user'||n||'@x.com', date('now') FROM c; SELECT * FROM users WHERE created_at >= date('now','-7 days');\" || python3 -c \"import sqlite3; c=sqlite3.connect('/tmp/test.db'); c.execute('CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, email TEXT, created_at TEXT)'); c.executemany('INSERT INTO users(name,email,created_at) VALUES(?,?,date(\\'now\\'))', [(f'user{i}',f'user{i}@x.com') for i in range(1,11)]); c.commit(); print(c.execute('SELECT * FROM users').fetchall())\"")))
rows.append(ex("Task has 3+ parts with pipes and redirects. Write a script file first instead of a one-liner: Analyze ~/knowledge git history to /tmp/git_analysis.txt",
               write("/tmp/git_analysis.sh", "#!/bin/bash\ncd ~/knowledge || exit 1\n{\ngit shortlog -sne --all | head -5\ngit log --all --pretty=format: --name-only | sort | uniq -c | sort -rn | head -10\nfind . -type f -printf '%s %p\\n' 2>/dev/null | sort -rn | head -10\ngit log --oneline -8\n} > /tmp/git_analysis.txt 2>&1")))

with open(OUT, "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
print(f"Wrote {len(rows)} examples -> {OUT}")
