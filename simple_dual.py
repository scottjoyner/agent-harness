#!/usr/bin/env python3
"""
Simple Dual-Model Agent
======================

Quick test of VibeThinker + MiniCPM5 LoRA synergy.
"""

import json
import time
import urllib.request
import subprocess

VIBETHINKER = "http://localhost:1234"
MINICPM5 = "http://localhost:1235"

def call(url, messages, max_tokens=300, tools=False):
    body = {"model": "test", "messages": messages, "max_tokens": max_tokens, "temperature": 0.1}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}}]
    
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{url}/v1/chat/completions", data=data, headers={"Content-Type": "application/json"})
    
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read())
        msg = result["choices"][0]["message"]
        return {
            "content": msg.get("content", ""),
            "reasoning": msg.get("reasoning_content", ""),
            "tool_calls": msg.get("tool_calls", []),
            "tokens": result["usage"]["completion_tokens"]
        }
    except Exception as e:
        return {"content": f"Error: {e}", "reasoning": "", "tool_calls": [], "tokens": 0}

def extract_bash(content):
    """Extract bash command from <function> XML."""
    if "<function" not in content:
        return None
    try:
        start = content.find('<param name="command">') + 22
        end = content.find('</param>', start)
        return content[start:end]
    except:
        return None

def run(task):
    print(f"\n{'='*50}")
    print(f"TASK: {task}")
    print(f"{'='*50}")
    
    # Step 1: VibeThinker plans
    print("\n[PLAN]")
    plan = call(VIBETHINKER, [
        {"role": "system", "content": "You are a planning assistant. Respond with a JSON object: {\"steps\": [\"step1\", \"step2\", ...]}"},
        {"role": "user", "content": task}
    ], max_tokens=400)
    
    try:
        steps = json.loads(plan['content'])['steps']
    except:
        steps = [task]
    
    for i, s in enumerate(steps[:3]):
        print(f"  {i+1}. {s}")
    
    # Step 2: MiniCPM5 executes
    print("\n[EXECUTE]")
    for i, step in enumerate(steps[:3]):
        print(f"\n  Step {i+1}: {step[:40]}...")
        
        result = call(MINICPM5, [
            {"role": "system", "content": "You are a tool-calling assistant. Always use <function> tags for tools."},
            {"role": "user", "content": step}
        ], tools=True)
        
        # Check for tool call
        cmd = extract_bash(result['content'])
        if cmd:
            print(f"    Tool: bash({cmd})")
            try:
                output = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                print(f"    Result: {output.stdout[:100]}")
            except subprocess.TimeoutExpired:
                print("    Result: (timeout)")
        elif result['tool_calls']:
            tc = result['tool_calls'][0]
            cmd = json.loads(tc['function']['arguments']).get('command', '')
            print(f"    Tool: bash({cmd})")
            try:
                output = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                print(f"    Result: {output.stdout[:100]}")
            except:
                print("    Result: (error)")
        else:
            print(f"    Output: {result['content'][:100]}")
    
    print(f"\n[DONE] Tokens: {plan['tokens'] + result['tokens']}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        run(" ".join(sys.argv[1:]))
    else:
        run("List files in current directory")
        run("Check system memory")
        run("Show running processes")
