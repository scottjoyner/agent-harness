#!/usr/bin/env python3
"""
Terminal Bench v4 - Fixed CDATA
===============================

Fixed CDATA handling for complex commands.
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
        "check": lambda: os.path.exists("date_script.py")
    },
    {
        "id": "C2",
        "instruction": "Count files in /home/scott/git (max depth 1)",
        "check": lambda: True
    },
    {
        "id": "C3",
        "instruction": "Create a directory called test_dir and put a file in it",
        "check": lambda: os.path.isdir("test_dir")
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
        "check": lambda: os.path.exists("numbers.txt")
    },
    {
        "id": "C8",
        "instruction": "Show disk usage for /home",
        "check": lambda: True
    },
]

EXECUTOR_PROMPT = """You are a tool-calling assistant. Execute tasks using bash.

RULES:
1. Output ONLY the function call
2. Format: <function name="bash"><param name="command">COMMAND</param></function>
3. Never explain, just execute
4. For multi-line scripts, use: cat << 'EOF' > file

Example:
<function name="bash"><param name="command">echo hello</param></function>"""

def call_model(port, messages, max_tokens=200):
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

def extract_command(content):
    """Extract bash command from function call, handling CDATA."""
    if "<function" not in content:
        return None
    
    try:
        # Try CDATA first
        cdata_match = re.search(r'<!\[CDATA\[(.*?)\]\]>', content, re.DOTALL)
        if cdata_match:
            return cdata_match.group(1).strip()
        
        # Try regular param
        match = re.search(r'<param\s+name="command">(.*?)</param>', content, re.DOTALL)
        if match:
            return match.group(1).strip()
    except:
        pass
    return None

def run_task(task):
    """Run a single task."""
    print(f"\n{'='*50}")
    print(f"TASK: {task['id']}")
    print(f"Instruction: {task['instruction']}")
    print(f"{'='*50}")
    
    messages = [
        {"role": "system", "content": EXECUTOR_PROMPT},
        {"role": "user", "content": task['instruction']}
    ]
    
    result = call_model(1235, messages)
    print(f"Response: {result['content'][:200]}")
    print(f"Speed: {result['speed']:.1f} tok/s")
    
    cmd = extract_command(result['content'])
    if cmd:
        print(f"Command: {cmd[:100]}")
        try:
            output = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            print(f"Output: {output.stdout[:100]}")
            success = output.returncode == 0
        except subprocess.TimeoutExpired:
            print("Command timed out")
            success = False
        except Exception as e:
            print(f"Error: {e}")
            success = False
    else:
        print("No command extracted")
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
    print(f"TERMINAL BENCH v4 - FIXED CDATA")
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
        "version": "4.0",
        "passed": passed,
        "total": total,
        "tokens": total_tokens,
        "time": total_time,
        "speed": avg_speed,
        "results": results
    }
    
    filename = f"bench_v4_{datetime.now().strftime('%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"Saved to {filename}")

if __name__ == "__main__":
    main()
