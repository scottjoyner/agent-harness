#!/usr/bin/env python3
"""
Terminal Bench v6 - Error Recovery
==================================

Improvements:
- Better error handling
- Retry on failure
- Tool availability checks
- Smarter prompts
"""

import json
import time
import urllib.request
import subprocess
import os
import re
from datetime import datetime

# Tasks
TASKS = [
    {
        "id": "C1",
        "instruction": "Create a Python script that prints the current date and time",
        "check": lambda: os.path.exists("date_script.py") and os.path.getsize("date_script.py") > 0
    },
    {
        "id": "C2",
        "instruction": "Count files in /home/scott/git (max depth 1)",
        "check": lambda: True
    },
    {
        "id": "C3",
        "instruction": "Create a directory called bench_test and put a file in it",
        "check": lambda: os.path.isdir("bench_test")
    },
    {
        "id": "C4",
        "instruction": "Check what processes are using the most memory",
        "check": lambda: True
    },
    {
        "id": "C5",
        "instruction": "Create a bash script that counts lines in Python files",
        "check": lambda: os.path.exists("count_lines.sh")
    },
    {
        "id": "C6",
        "instruction": "List all listening network ports",
        "check": lambda: True
    },
    {
        "id": "C7",
        "instruction": "Create a file with numbers 1 to 10",
        "check": lambda: os.path.exists("numbers.txt") and open("numbers.txt").read().strip() == "1\n2\n3\n4\n5\n6\n7\n8\n9\n10"
    },
    {
        "id": "C8",
        "instruction": "Show disk usage for /home",
        "check": lambda: True
    },
]

EXECUTOR_PROMPT = """You are a tool-calling assistant for terminal tasks.

RULES:
1. Output ONLY the function call
2. Format: <function name="bash"><param name="command">COMMAND</param></function>
3. Never explain, just execute
4. Chain multiple commands with &&
5. Use ss instead of netstat for network ports
6. For scripts, use .sh extension for bash, .py for Python
7. If a directory might exist, use mkdir -p

Examples:
- List ports: <function name="bash"><param name="command">ss -tulnp</param></function>
- Create dir: <function name="bash"><param name="command">mkdir -p mydir</param></function>
- Create script: <function name="bash"><param name="command">cat << 'EOF' > script.sh
#!/bin/bash
echo hello
EOF
chmod +x script.sh</param></function>"""

def call_model(port, messages, max_tokens=300):
    body = {
        "model": "test",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1
    }
    
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"http://localhost:{port}/v1/chat/completions", 
                                data=data, headers={"Content-Type": "application/json"})
    
    start = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        msg = result["choices"][0]["message"]
        return {
            "content": msg.get("content", ""),
            "tokens": result["usage"]["completion_tokens"],
            "speed": result["timings"]["predicted_per_second"],
            "time": time.time() - start
        }
    except Exception as e:
        return {
            "content": f"Error: {e}",
            "tokens": 0,
            "speed": 0,
            "time": time.time() - start
        }

def extract_commands(content):
    """Extract bash commands from function calls."""
    if "<function" not in content:
        return []
    
    commands = []
    
    # Find all function calls
    for match in re.finditer(r'<function\s+name="bash">\s*<param\s+name="command">(.*?)</param>', content, re.DOTALL):
        cmd = match.group(1).strip()
        # Remove CDATA if present
        if cmd.startswith('<![CDATA['):
            cmd = cmd[9:]
        if cmd.endswith(']]>'):
            cmd = cmd[:-3]
        commands.append(cmd)
    
    return commands

def run_command(cmd, timeout=30):
    """Run a command with error handling."""
    try:
        # Replace netstat with ss
        cmd = cmd.replace('netstat', 'ss')
        
        output = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {
            "stdout": output.stdout,
            "stderr": output.stderr,
            "returncode": output.returncode,
            "success": output.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "Command timed out",
            "returncode": -1,
            "success": False
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "success": False
        }

def run_task(task, retry=True):
    """Run a single task with retry."""
    print(f"\n{'='*50}")
    print(f"TASK: {task['id']}")
    print(f"Instruction: {task['instruction']}")
    print(f"{'='*50}")
    
    messages = [
        {"role": "system", "content": EXECUTOR_PROMPT},
        {"role": "user", "content": task['instruction']}
    ]
    
    result = call_model(1235, messages)
    print(f"Response: {result['content'][:250]}")
    print(f"Speed: {result['speed']:.1f} tok/s")
    
    commands = extract_commands(result['content'])
    
    if commands:
        print(f"Commands found: {len(commands)}")
        
        # Chain commands with &&
        chained = " && ".join(commands)
        print(f"Chained: {chained[:150]}")
        
        cmd_result = run_command(chained)
        print(f"Output: {cmd_result['stdout'][:100]}")
        if cmd_result['stderr']:
            print(f"Stderr: {cmd_result['stderr'][:100]}")
        
        success = cmd_result['success']
        
        # Retry on failure
        if not success and retry:
            print("Retrying...")
            time.sleep(1)
            
            # Try simpler command
            if len(commands) > 1:
                chained = commands[0]  # Just first command
                cmd_result = run_command(chained)
                success = cmd_result['success']
    else:
        print("No commands found")
        success = False
    
    try:
        check_passed = task['check']()
    except Exception as e:
        print(f"Check error: {e}")
        check_passed = False
    
    final_success = success and check_passed
    print(f"Result: {'PASS' if final_success else 'FAIL'}")
    
    return {
        "id": task['id'],
        "success": final_success,
        "tokens": result['tokens'],
        "speed": result['speed'],
        "time": result['time']
    }

def main():
    print(f"\n{'='*60}")
    print(f"TERMINAL BENCH v6 - ERROR RECOVERY")
    print(f"{'='*60}")
    print(f"Time: {datetime.now().isoformat()}")
    print(f"Tasks: {len(TASKS)}")
    print(f"{'='*60}")
    
    results = []
    total_tokens = 0
    total_time = 0
    
    for task in TASKS:
        result = run_task(task)
        results.append(result)
        total_tokens += result['tokens']
        total_time += result['time']
        time.sleep(0.5)
    
    passed = sum(1 for r in results if r['success'])
    total = len(results)
    avg_speed = sum(r['speed'] for r in results) / total if total > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Passed: {passed}/{total} ({(passed/total*100):.0f}%)")
    print(f"Total tokens: {total_tokens}")
    print(f"Total time: {total_time:.1f}s")
    print(f"Avg speed: {avg_speed:.1f} tok/s")
    print(f"{'='*60}")
    
    summary = {
        "timestamp": datetime.now().isoformat(),
        "version": "6.0",
        "passed": passed,
        "total": total,
        "tokens": total_tokens,
        "time": total_time,
        "speed": avg_speed,
        "results": results
    }
    
    filename = f"bench_v6_{datetime.now().strftime('%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"Saved to {filename}")

if __name__ == "__main__":
    main()
