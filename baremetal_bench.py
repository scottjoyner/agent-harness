#!/usr/bin/env python3
"""
BareMetal Benchmark Suite v1.0
Real-world terminal tasks without Docker overhead.
"""

import json
import os
import sys
import time
import subprocess
import requests
from datetime import datetime
from pathlib import Path

# Model endpoints on x1-370 (Tailscale: 100.64.43.123)
X1_370_URL = "http://100.64.43.123:1234/v1/chat/completions"
MINICPM5_URL = X1_370_URL  # minicpm5-2b (base FP16)
VIBETHINKER_URL = X1_370_URL  # toolcall-v5-3b-combined-r2 (latest finetune)

# Model names
MINICPM5_MODEL = "minicpm5-2b"
VIBETHINKER_MODEL = "toolcall-v5-3b-combined-r2"

SYSTEM_PROMPT = """You are a bash coding agent. Respond with a single bash command in this exact format:
<function name="bash"><param name="command">COMMAND</param></function>

Rules:
- One command only
- Chain with && or ;
- Use cat << 'EOF' for scripts
- No explanation"""

def load_tasks():
    """Load tasks from JSON file."""
    task_file = Path(__file__).parent / "benchmarks" / "baremetal" / "tasks.json"
    with open(task_file) as f:
        data = json.load(f)
    return data["tasks"]

def call_model(instruction, timeout=120):
    """Call MiniCPM5 model (local Q4 or remote FP16)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": instruction}
    ]
    
    payload = {
        "model": "minicpm5-2b",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 300,
        "stream": False
    }
    
    try:
        response = requests.post(MINICPM5_URL, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        reasoning = data["choices"][0]["message"].get("reasoning_content", "")
        
        # The FP16 model puts reasoning in content sometimes
        # Check both for commands
        for text in [content, reasoning]:
            if text:
                cmd = extract_command(text)
                if cmd:
                    return f"EXTRACTED:{cmd}"
        
        # Return combined for debugging
        return f"CONTENT:{content}\nREASONING:{reasoning[:200]}"
    except Exception as e:
        return f"ERROR: {e}"

def extract_command(response):
    """Extract bash command from model response."""
    import re
    
    if not response or response.startswith("ERROR"):
        return None
    
    # Try CDATA format (most reliable)
    cdata_match = re.search(r'<!\[CDATA\[(.*?)\]\]>', response, re.DOTALL)
    if cdata_match:
        return cdata_match.group(1).strip()
    
    # Try standard function call format
    cmd_match = re.search(r'<param name="command">(.*?)</param>', response, re.DOTALL)
    if cmd_match:
        return cmd_match.group(1).strip()
    
    # Try markdown code blocks
    code_match = re.search(r'```(?:bash|sh)?\n(.*?)```', response, re.DOTALL)
    if code_match:
        return code_match.group(1).strip()
    
    # Try to find command after common prefixes
    for prefix in ['Run:', 'Execute:', 'Command:', '$ ', 'Use: ']:
        if prefix in response:
            idx = response.index(prefix) + len(prefix)
            cmd = response[idx:].split('\n')[0].strip()
            if cmd and len(cmd) > 3:
                return cmd
    
    # Look for lines that look like commands
    lines = response.split('\n')
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('<') or line.startswith('```'):
            continue
        # Check if it looks like a command
        if any(line.startswith(p) for p in ['echo', 'cat', 'ls', 'find', 'grep', 'python', 'pip', 'sudo', 'cd', 'mkdir', 'rm', 'cp', 'mv', 'curl', 'wget', 'git', 'chmod', 'touch', 'head', 'tail', 'wc', 'sort', 'uniq']):
            return line
    
    # Try inline code
    inline_match = re.search(r'`([^`]+)`', response)
    if inline_match:
        cmd = inline_match.group(1)
        if any(cmd.startswith(p) for p in ['echo', 'cat', 'ls', 'find', 'grep', 'python', 'pip', 'sudo', 'cd', 'mkdir', 'rm', 'cp', 'mv']):
            return cmd
    
    return None

def run_command(cmd, timeout=30):
    """Run a bash command safely."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "TIMEOUT", -1
    except Exception as e:
        return str(e), -1

def verify_task(task, output, returncode):
    """Verify if task was completed successfully."""
    verification = task.get("verification", {})
    v_type = verification.get("type", "file_exists")
    
    if v_type == "file_exists":
        path = Path(verification["path"])
        if not path.exists():
            return False, f"File {path} does not exist"
        if "min_size_bytes" in verification:
            if path.stat().st_size < verification["min_size_bytes"]:
                return False, f"File too small: {path.stat().st_size} < {verification['min_size_bytes']} bytes"
        return True, "PASS"
    
    elif v_type == "directory_exists":
        path = Path(verification["path"])
        if not path.exists():
            return False, f"Directory {path} does not exist"
        if "min_files" in verification:
            file_count = len(list(path.iterdir()))
            if file_count < verification["min_files"]:
                return False, f"Too few files: {file_count} < {verification['min_files']}"
        return True, "PASS"
    
    elif v_type == "command_success":
        return returncode == 0, f"Return code: {returncode}"
    
    return True, "No verification"

def run_benchmark(tasks=None, max_tasks=None):
    """Run the benchmark suite."""
    all_tasks = load_tasks()
    
    if tasks:
        all_tasks = [t for t in all_tasks if t["id"] in tasks]
    
    if max_tasks:
        all_tasks = all_tasks[:max_tasks]
    
    print("=" * 60)
    print("BAREMETAL BENCHMARK SUITE v1.0")
    print("=" * 60)
    print(f"Time: {datetime.now().isoformat()}")
    print(f"Tasks: {len(all_tasks)}")
    print("=" * 60)
    
    results = []
    passed = 0
    total_tokens = 0
    total_time = 0
    
    for task in all_tasks:
        print(f"\n{'='*60}")
        print(f"TASK: {task['id']} - {task['name']}")
        print(f"Instruction: {task['instruction'][:100]}...")
        print(f"{'='*60}")
        
        # Clean up previous artifacts
        verification = task.get("verification", {})
        if "path" in verification:
            path = Path(verification["path"])
            if path.exists():
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    import shutil
                    shutil.rmtree(path)
        
        # Call model
        start_time = time.time()
        response = call_model(task["instruction"])
        model_time = time.time() - start_time
        
        print(f"Response: {response[:200]}...")
        print(f"Model time: {model_time:.1f}s")
        
        # Extract and run command
        cmd = extract_command(response)
        if cmd:
            print(f"Command: {cmd[:100]}...")
            output, returncode = run_command(cmd, timeout=task.get("timeout_sec", 60))
            print(f"Output: {output[:200] if output else 'none'}...")
            
            # Verify
            success, message = verify_task(task, output, returncode)
            print(f"Result: {'PASS' if success else 'FAIL'} - {message}")
            
            if success:
                passed += 1
            
            results.append({
                "task_id": task["id"],
                "task_name": task["name"],
                "success": success,
                "message": message,
                "model_time": model_time,
                "command": cmd[:200],
                "output": output[:500] if output else ""
            })
        else:
            print("Failed to extract command")
            results.append({
                "task_id": task["id"],
                "task_name": task["name"],
                "success": False,
                "message": "Failed to extract command",
                "model_time": model_time,
                "command": "",
                "output": response[:500]
            })
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Passed: {passed}/{len(all_tasks)} ({100*passed/len(all_tasks):.1f}%)")
    print(f"Total time: {sum(r['model_time'] for r in results):.1f}s")
    print("=" * 60)
    
    # Save results
    output_file = f"baremetal_results_{datetime.now().strftime('%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "tasks_total": len(all_tasks),
            "tasks_passed": passed,
            "results": results
        }, f, indent=2)
    
    print(f"\nResults saved to {output_file}")
    return passed, len(all_tasks)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", nargs="+", help="Specific task IDs to run")
    parser.add_argument("--max", type=int, help="Max number of tasks")
    args = parser.parse_args()
    
    run_benchmark(tasks=args.tasks, max_tasks=args.max)
