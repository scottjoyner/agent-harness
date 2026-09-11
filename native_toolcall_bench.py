#!/usr/bin/env python3
"""
Native Tool Calling Harness for x1-370 models.
Uses the OpenAI-compatible tool calling API for both models.
"""

import requests
import time
import re
import json
import subprocess
from datetime import datetime
from pathlib import Path

X1_370_URL = "http://100.64.43.123:1234/v1/chat/completions"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a bash command",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute"
                }
            },
            "required": ["command"]
        }
    }
}]

def call_model(model_name, instruction, timeout=60):
    """Call model with native tool calling."""
    messages = [{"role": "user", "content": instruction}]
    
    payload = {
        "model": model_name,
        "messages": messages,
        "tools": TOOLS,
        "temperature": 0.1,
        "max_tokens": 500,
        "stream": False
    }
    
    start = time.time()
    try:
        response = requests.post(X1_370_URL, json=payload, timeout=timeout)
        elapsed = time.time() - start
        data = response.json()
        
        choice = data["choices"][0]
        message = choice["message"]
        tool_calls = message.get("tool_calls", [])
        content = message.get("content", "")
        finish_reason = choice.get("finish_reason", "")
        
        return {
            "tool_calls": tool_calls,
            "content": content,
            "finish_reason": finish_reason,
            "time": elapsed,
            "usage": data.get("usage", {}),
            "raw": message
        }
    except Exception as e:
        return {"error": str(e), "time": time.time() - start}

def extract_command(result):
    """Extract command from tool call result."""
    if "error" in result:
        return None
    
    tool_calls = result.get("tool_calls", [])
    if tool_calls:
        args = tool_calls[0].get("function", {}).get("arguments", "")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except:
                return None
        return args.get("command")
    
    # Fallback to content parsing
    content = result.get("content", "")
    if content:
        m = re.search(r'```(?:bash|sh)?\n(.*?)```', content, re.DOTALL)
        if m:
            return m.group(1).strip()
    
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
    
    return True, "No verification"

def run_benchmark(model_name, tasks=None, max_tasks=None):
    """Run benchmark with a specific model."""
    # Load tasks
    task_file = Path(__file__).parent / "benchmarks" / "baremetal" / "tasks.json"
    with open(task_file) as f:
        all_tasks = json.load(f)["tasks"]
    
    if tasks:
        all_tasks = [t for t in all_tasks if t["id"] in tasks]
    if max_tasks:
        all_tasks = all_tasks[:max_tasks]
    
    print("=" * 60)
    print(f"NATIVE TOOL CALLING BENCHMARK - {model_name}")
    print("=" * 60)
    print(f"Time: {datetime.now().isoformat()}")
    print(f"Tasks: {len(all_tasks)}")
    print("=" * 60)
    
    results = []
    passed = 0
    
    for task in all_tasks:
        print(f"\n{'='*60}")
        print(f"TASK: {task['id']} - {task['name']}")
        print(f"Instruction: {task['instruction'][:80]}...")
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
        result = call_model(model_name, task["instruction"])
        cmd = extract_command(result)
        
        if cmd:
            print(f"Command: {cmd[:80]}...")
            output, returncode = run_command(cmd, timeout=task.get("timeout_sec", 60))
            success, message = verify_task(task, output, returncode)
            print(f"Result: {'PASS' if success else 'FAIL'} - {message}")
            
            if success:
                passed += 1
        else:
            print(f"No command extracted. Finish: {result.get('finish_reason', '?')}")
            success = False
            message = "No command extracted"
            cmd = ""
            output = result.get("content", "")[:500]
        
        results.append({
            "task_id": task["id"],
            "task_name": task["name"],
            "success": success,
            "message": message,
            "model_time": result.get("time", 0),
            "command": cmd[:200] if cmd else "",
            "output": output[:500] if output else ""
        })
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Model: {model_name}")
    print(f"Passed: {passed}/{len(all_tasks)} ({100*passed/len(all_tasks):.1f}%)")
    print(f"Avg time: {sum(r['model_time'] for r in results)/len(results):.1f}s")
    print("=" * 60)
    
    # Save results
    output_file = f"native_toolcall_{model_name}_{datetime.now().strftime('%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "model": model_name,
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
    parser.add_argument("--model", default="minicpm5-2b", 
                       choices=["minicpm5-2b", "toolcall-v5-3b-combined-r2"],
                       help="Model to test")
    parser.add_argument("--tasks", nargs="+", help="Specific task IDs to run")
    parser.add_argument("--max", type=int, help="Max number of tasks")
    args = parser.parse_args()
    
    run_benchmark(args.model, tasks=args.tasks, max_tasks=args.max)
