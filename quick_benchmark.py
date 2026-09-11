#!/usr/bin/env python3
"""
Quick Benchmark
===============

Fast benchmark for comparing models.
"""

import json
import time
import urllib.request
from datetime import datetime

def test_model(port, prompt, max_tokens=200):
    body = json.dumps({
        "model": "test",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Always use tools when needed."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1
    }).encode()
    
    req = urllib.request.Request(f"http://localhost:{port}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
    
    start = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        msg = result["choices"][0]["message"]
        content = msg.get("content", "")
        tokens = result["usage"]["completion_tokens"]
        t = result["timings"]
        
        return {
            "content": content[:150],
            "tokens": tokens,
            "speed": t["predicted_per_second"],
            "prompt_speed": t["prompt_per_second"],
            "time": time.time() - start,
            "success": True
        }
    except Exception as e:
        return {
            "content": str(e)[:100],
            "tokens": 0,
            "speed": 0,
            "prompt_speed": 0,
            "time": time.time() - start,
            "success": False
        }

def run_benchmark(port, name):
    print(f"\n{'='*60}")
    print(f"BENCHMARK: {name}")
    print(f"{'='*60}")
    
    tasks = [
        ("Tool call", "Run 'echo test'"),
        ("File read", "Read /etc/hostname"),
        ("File list", "List files in /home"),
        ("Explanation", "Explain what a variable is"),
        ("Planning", "Plan a simple script"),
    ]
    
    results = []
    for task_name, prompt in tasks:
        print(f"\n{task_name}: {prompt[:30]}...")
        result = test_model(port, prompt)
        results.append(result)
        
        if result["success"]:
            has_fn = "<function" in result["content"]
            print(f"  Speed: {result['speed']:.1f} tok/s | Tokens: {result['tokens']} | Tool: {'Yes' if has_fn else 'No'}")
        else:
            print(f"  ERROR: {result['content']}")
    
    # Summary
    passed = sum(1 for r in results if r["success"])
    avg_speed = sum(r["speed"] for r in results if r["success"]) / max(1, passed)
    tool_calls = sum(1 for r in results if "<function" in r.get("content", ""))
    
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Tasks: {passed}/{len(results)}")
    print(f"Avg speed: {avg_speed:.1f} tok/s")
    print(f"Tool calls: {tool_calls}/{passed}")
    print(f"{'='*60}")
    
    return {
        "name": name,
        "timestamp": datetime.now().isoformat(),
        "tasks": len(results),
        "passed": passed,
        "avg_speed": avg_speed,
        "tool_calls": tool_calls,
        "results": results
    }

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python3 quick_benchmark.py <port> <name>")
        print("Example: python3 quick_benchmark.py 1235 MiniCPM5-LoRA")
        sys.exit(1)
    
    port = int(sys.argv[1])
    name = sys.argv[2]
    
    result = run_benchmark(port, name)
    
    # Save
    filename = f"benchmark_{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved to {filename}")
