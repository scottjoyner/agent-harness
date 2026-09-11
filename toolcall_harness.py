#!/usr/bin/env python3
"""
ToolCall Harness for toolcall-v5-3b-combined-r2
Handles the XML-style <tool_call> format output by this model.
"""

import requests
import time
import re
import json
import subprocess
from datetime import datetime
from pathlib import Path

X1_370_URL = "http://100.64.43.123:1234/v1/chat/completions"

# System prompt that matches the model's training format
SYSTEM_PROMPT = """You are a helpful assistant with access to a bash tool. When you need to execute a command, use the bash tool.

Example:
<tool_call name="bash" call_id="call_123">
{"command": "ls -la"}
</tool_call>"""

def call_model(instruction, timeout=120, max_tokens=2000):
    """Call toolcall model and parse XML tool_call format."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": instruction}
    ]
    
    payload = {
        "model": "toolcall-v5-3b-combined-r2",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False
    }
    
    start = time.time()
    try:
        response = requests.post(X1_370_URL, json=payload, timeout=timeout)
        elapsed = time.time() - start
        data = response.json()
        
        content = data["choices"][0]["message"]["content"]
        reasoning = data["choices"][0]["message"].get("reasoning_content", "")
        finish = data["choices"][0].get("finish_reason", "")
        usage = data.get("usage", {})
        
        # Parse XML tool_call from content
        command = extract_tool_call(content)
        
        return {
            "command": command,
            "content": content,
            "reasoning": reasoning,
            "finish_reason": finish,
            "time": elapsed,
            "usage": usage
        }
    except Exception as e:
        return {"error": str(e), "time": time.time() - start}

def extract_tool_call(text):
    """Extract command from XML <tool_call> format."""
    if not text:
        return None
    
    # Try multiple tool names
    for tool_name in ['bash', 'terminal', 'write_file', 'execute']:
        # Find the start of tool_call
        pattern = f'<tool_call\\s+name="{tool_name}"[^>]*>\\s*'
        start_match = re.search(pattern, text)
        if not start_match:
            continue
        
        # Get everything after the opening tag
        remaining = text[start_match.end():]
        
        # Find the end - look for </tool_call> or end of text
        end_match = re.search(r'\s*</tool_call>', remaining)
        if end_match:
            json_str = remaining[:end_match.start()]
        else:
            json_str = remaining
        
        # Try to parse as JSON with fixes
        try:
            # Fix unescaped newlines
            fixed_json = json_str.replace('\n', '\\n')
            args = json.loads(fixed_json)
            cmd = args.get("command", "")
            if cmd:
                cmd = cmd.replace('\\n', '\n').replace('\\"', '"')
                return cmd
        except json.JSONDecodeError:
            pass
        
        # Try to extract command from JSON-like text
        cmd_match = re.search(r'"command"\s*:\s*"((?:[^"\\]|\\.)*)"', json_str, re.DOTALL)
        if cmd_match:
            cmd = cmd_match.group(1)
            cmd = cmd.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
            return cmd
    
    # Fallback: look for any <tool_call> and extract content
    start_match = re.search(r'<tool_call[^>]*>\s*', text)
    if start_match:
        remaining = text[start_match.end():]
        end_match = re.search(r'\s*</tool_call>', remaining)
        if end_match:
            json_str = remaining[:end_match.start()]
        else:
            json_str = remaining
        
        # Try to find command in any format
        cmd_match = re.search(r'"command"\s*:\s*"((?:[^"\\]|\\.)*)"', json_str, re.DOTALL)
        if cmd_match:
            cmd = cmd_match.group(1)
            cmd = cmd.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
            return cmd
    
    # Last resort: look for actual command in the text
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line or line.startswith('<') or line.startswith('#') or line.startswith('{') or line.startswith('}') or line.startswith('"'):
            continue
        if any(line.startswith(p) for p in ['echo', 'cat', 'ls', 'find', 'grep', 'python', 'pip', 'sudo', 'cd', 'mkdir', 'rm', 'cp', 'mv', 'curl', 'wget', 'git', 'chmod', 'touch', 'head', 'tail', 'wc', 'sort', 'uniq', 'ps', 'free', 'df', 'du', 'uname', 'docker', 'bash', 'sh']):
            return line
    
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

def run_benchmark(tasks=None, max_tasks=None):
    """Run benchmark with toolcall model."""
    # Load tasks
    task_file = Path(__file__).parent / "benchmarks" / "baremetal" / "tasks.json"
    with open(task_file) as f:
        all_tasks = json.load(f)["tasks"]
    
    if tasks:
        all_tasks = [t for t in all_tasks if t["id"] in tasks]
    if max_tasks:
        all_tasks = all_tasks[:max_tasks]
    
    print("=" * 60)
    print("TOOLCALL-V5-3B-COMBINED-R2 HARNESS")
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
        result = call_model(task["instruction"])
        cmd = result.get("command")
        
        if cmd:
            print(f"Command: {cmd[:80]}...")
            output, returncode = run_command(cmd, timeout=task.get("timeout_sec", 60))
            success, message = verify_task(task, output, returncode)
            print(f"Result: {'PASS' if success else 'FAIL'} - {message}")
            
            if success:
                passed += 1
        else:
            print(f"No command extracted")
            print(f"Content: {result.get('content', '')[:200]}")
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
    print(f"Passed: {passed}/{len(all_tasks)} ({100*passed/len(all_tasks):.1f}%)")
    print(f"Avg time: {sum(r['model_time'] for r in results)/len(results):.1f}s")
    print("=" * 60)
    
    # Save results
    output_file = f"toolcall_harness_{datetime.now().strftime('%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "model": "toolcall-v5-3b-combined-r2",
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
