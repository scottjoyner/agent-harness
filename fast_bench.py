#!/usr/bin/env python3
"""
Fast Terminal Bench
===================

Quick benchmark for iterating on harness improvements.
"""

import json
import time
import urllib.request
import subprocess
import os
import re
from datetime import datetime

# Simple test tasks
TASKS = [
    {
        "id": "T1",
        "instruction": "Create a file called test.txt with content 'hello world'",
        "check": lambda: os.path.exists("test.txt") and open("test.txt").read() == "hello world"
    },
    {
        "id": "T2", 
        "instruction": "Read the file /etc/hostname",
        "check": lambda: True  # Just check it runs
    },
    {
        "id": "T3",
        "instruction": "List all Python files in /home/scott",
        "check": lambda: True  # Just check it runs
    },
    {
        "id": "T4",
        "instruction": "Run 'uname -a' to get system info",
        "check": lambda: True  # Just check it runs
    },
    {
        "id": "T5",
        "instruction": "Check disk usage with df -h",
        "check": lambda: True  # Just check it runs
    },
]

EXECUTOR_PROMPT = """You are a tool-calling assistant. Execute tasks using tools.

When you need to use a tool, respond with ONLY a function call:
<function name="bash"><param name="command">your_command</param></function>

Available tools:
- bash: Execute shell commands (param: command)
- read_file: Read files (param: path)

Never explain. Just call the tool."""

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
    """Extract bash command from function call."""
    if "<function" not in content:
        return None
    
    try:
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
    
    # Call MiniCPM5
    messages = [
        {"role": "system", "content": EXECUTOR_PROMPT},
        {"role": "user", "content": task['instruction']}
    ]
    
    result = call_model(1235, messages)
    print(f"Response: {result['content'][:100]}")
    print(f"Speed: {result['speed']:.1f} tok/s")
    
    # Extract and execute command
    cmd = extract_command(result['content'])
    if cmd:
        print(f"Command: {cmd}")
        try:
            output = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            print(f"Output: {output.stdout[:100]}")
            success = output.returncode == 0
        except Exception as e:
            print(f"Error: {e}")
            success = False
    else:
        print("No command extracted")
        success = False
    
    # Check result
    try:
        check_passed = task['check']()
    except:
        check_passed = False
    
    print(f"Result: {'PASS' if success and check_passed else 'FAIL'}")
    
    return {
        "id": task['id'],
        "success": success and check_passed,
        "tokens": result['tokens'],
        "speed": result['speed'],
        "time": result['time']
    }

def main():
    print(f"\n{'='*60}")
    print(f"FAST TERMINAL BENCH")
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
        time.sleep(0.5)  # Brief pause
    
    # Summary
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
    
    # Save results
    summary = {
        "timestamp": datetime.now().isoformat(),
        "passed": passed,
        "total": total,
        "tokens": total_tokens,
        "time": total_time,
        "speed": avg_speed,
        "results": results
    }
    
    filename = f"bench_{datetime.now().strftime('%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"Saved to {filename}")

if __name__ == "__main__":
    main()
