#!/usr/bin/env python3
"""
Synergistic Agent Harness
========================

Practical harness for VibeThinker + MiniCPM5 LoRA working together.

Usage:
    python3 synergistic_harness.py "your task here"
    python3 synergistic_harness.py --interactive
"""

import json
import sys
import time
import urllib.request
import subprocess
import os
from typing import Dict, List, Optional

# Model endpoints
VIBETHINKER = "http://localhost:1234"
MINICPM5 = "http://localhost:1235"

# System prompts
REASONER_SYSTEM = """You are a reasoning assistant. Analyze requests and output JSON plans.

When given a task, respond with a JSON object:
{
    "analysis": "brief analysis of the task",
    "steps": [
        {"action": "description", "tool": "bash|read|write|search", "params": {...}},
        ...
    ],
    "success_criteria": "how to know if task is complete"
}

Be concise. Output only valid JSON."""

EXECUTOR_SYSTEM = """You are a tool-calling assistant. Execute tasks using tools.

When you need to use a tool, respond with:
<function name="tool_name"><param name="param_name">value</param></function>

Available tools:
- bash: Execute shell commands (param: command)
- read: Read files (param: path)  
- write: Write files (param: path, content)
- search: Search files (param: pattern, path)

Always use tools directly. Never explain what you're going to do."""

def call_model(url: str, messages: List[Dict], tools: bool = False, max_tokens: int = 500) -> Dict:
    """Call a model endpoint."""
    body = {
        "model": "default",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1
    }
    
    if tools:
        body["tools"] = [
            {"type": "function", "function": {"name": "bash", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}},
            {"type": "function", "function": {"name": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
            {"type": "function", "function": {"name": "write", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}},
            {"type": "function", "function": {"name": "search", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}}}}
        ]
    
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{url}/v1/chat/completions", data=data, headers={"Content-Type": "application/json"})
    
    start = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read())
        msg = result["choices"][0]["message"]
        return {
            "content": msg.get("content", ""),
            "reasoning": msg.get("reasoning_content", ""),
            "tool_calls": msg.get("tool_calls", []),
            "tokens": result["usage"]["completion_tokens"],
            "time": time.time() - start
        }
    except Exception as e:
        return {"content": f"Error: {e}", "reasoning": "", "tool_calls": [], "tokens": 0, "time": time.time() - start}

def extract_tool_call(content: str) -> Optional[Dict]:
    """Extract tool call from MiniCPM5 XML output."""
    if "<function" not in content:
        return None
    
    # Simple XML parsing
    try:
        name_start = content.find('name="') + 6
        name_end = content.find('"', name_start)
        name = content[name_start:name_end]
        
        params = {}
        # Find all params
        param_start = 0
        while True:
            param_pos = content.find('<param name="', param_start)
            if param_pos == -1:
                break
            
            pname_start = param_pos + 13
            pname_end = content.find('"', pname_start)
            pname = content[pname_start:pname_end]
            
            value_start = content.find('>', pname_end) + 1
            value_end = content.find('</param>', value_start)
            value = content[value_start:value_end]
            
            params[pname] = value
            param_start = value_end + 8
        
        return {"name": name, "arguments": params}
    except:
        return None

def execute_tool(tool_call: Dict) -> str:
    """Execute a tool call."""
    name = tool_call["name"]
    args = tool_call["arguments"]
    
    try:
        if name == "bash":
            result = subprocess.run(args["command"], shell=True, capture_output=True, text=True, timeout=30)
            return result.stdout + result.stderr
        elif name == "read":
            with open(args["path"], 'r') as f:
                return f.read()
        elif name == "write":
            with open(args["path"], 'w') as f:
                f.write(args.get("content", ""))
            return f"File written: {args['path']}"
        elif name == "search":
            result = subprocess.run(f"find {args.get('path', '.')} -name '{args['pattern']}'", 
                                  shell=True, capture_output=True, text=True, timeout=10)
            return result.stdout
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"

def run_task(task: str) -> Dict:
    """Run a task using both models synergistically."""
    print(f"\n{'='*60}")
    print(f"TASK: {task}")
    print(f"{'='*60}")
    
    # Phase 1: VibeThinker plans
    print("\n[1] Planning (VibeThinker)...")
    plan_result = call_model(VIBETHINKER, [
        {"role": "system", "content": REASONER_SYSTEM},
        {"role": "user", "content": task}
    ], max_tokens=600)
    
    print(f"    Plan: {plan_result['content'][:200]}")
    
    # Try to parse plan
    try:
        plan = json.loads(plan_result['content'])
        steps = plan.get('steps', [])
    except:
        # If JSON fails, treat entire response as single step
        steps = [{"action": task, "tool": "bash", "params": {"command": task}}]
    
    print(f"    Steps: {len(steps)}")
    
    # Phase 2: MiniCPM5 executes
    print("\n[2] Executing (MiniCPM5 LoRA)...")
    results = []
    context = ""
    
    for i, step in enumerate(steps[:5]):  # Max 5 steps
        action = step.get('action', str(step))
        print(f"\n    Step {i+1}: {action[:50]}...")
        
        exec_result = call_model(MINICPM5, [
            {"role": "system", "content": EXECUTOR_SYSTEM + f"\n\nContext: {context}"},
            {"role": "user", "content": action}
        ], tools=True, max_tokens=300)
        
        # Check for tool call in content
        tool_call = extract_tool_call(exec_result['content'])
        
        if tool_call:
            print(f"    Tool: {tool_call['name']}({tool_call['arguments']})")
            tool_result = execute_tool(tool_call)
            print(f"    Result: {tool_result[:100]}")
            context += f"\nStep {i+1}: {tool_result[:200]}"
            results.append({"step": i+1, "tool": tool_call['name'], "result": tool_result[:500]})
        elif exec_result['tool_calls']:
            # OpenAI format tool calls
            tc = exec_result['tool_calls'][0]
            tool_call = {"name": tc['function']['name'], "arguments": json.loads(tc['function']['arguments'])}
            print(f"    Tool: {tool_call['name']}({tool_call['arguments']})")
            tool_result = execute_tool(tool_call)
            print(f"    Result: {tool_result[:100]}")
            context += f"\nStep {i+1}: {tool_result[:200]}"
            results.append({"step": i+1, "tool": tool_call['name'], "result": tool_result[:500]})
        else:
            print(f"    Output: {exec_result['content'][:100]}")
            context += f"\nStep {i+1}: {exec_result['content'][:200]}"
            results.append({"step": i+1, "output": exec_result['content'][:500]})
    
    # Phase 3: Evaluate
    print("\n[3] Evaluating (VibeThinker)...")
    eval_result = call_model(VIBETHINKER, [
        {"role": "system", "content": "You are an evaluation assistant. Respond with JSON: {\"complete\": true/false, \"summary\": \"brief summary\"}"},
        {"role": "user", "content": f"Task: {task}\n\nResults:\n{json.dumps(results, indent=2)}"}
    ], max_tokens=200)
    
    print(f"    Evaluation: {eval_result['content'][:200]}")
    
    return {
        "task": task,
        "plan": plan_result['content'],
        "results": results,
        "evaluation": eval_result['content'],
        "total_tokens": plan_result['tokens'] + sum(r.get('tokens', 0) for r in results) + eval_result['tokens'],
        "total_time": plan_result['time'] + sum(r.get('time', 0) for r in results) + eval_result['time']
    }

def interactive_mode():
    """Run in interactive mode."""
    print("\n" + "="*60)
    print("SYNERGISTIC AGENT HARNESS")
    print("VibeThinker (reasoning) + MiniCPM5 LoRA (execution)")
    print("="*60)
    print("Type 'quit' to exit, 'status' to check models\n")
    
    while True:
        try:
            task = input("Task> ").strip()
            if task.lower() == 'quit':
                break
            if task.lower() == 'status':
                # Check both models
                for name, url in [("VibeThinker", VIBETHINKER), ("MiniCPM5", MINICPM5)]:
                    try:
                        urllib.request.urlopen(f"{url}/v1/models", timeout=5)
                        print(f"  {name}: ONLINE")
                    except:
                        print(f"  {name}: OFFLINE")
                continue
            if not task:
                continue
            
            result = run_task(task)
            
            print(f"\n{'='*60}")
            print(f"COMPLETE | Tokens: {result['total_tokens']} | Time: {result['total_time']:.1f}s")
            print(f"{'='*60}\n")
            
        except KeyboardInterrupt:
            break
        except EOFError:
            break
    
    print("\nGoodbye!")

def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--interactive":
            interactive_mode()
        else:
            task = " ".join(sys.argv[1:])
            result = run_task(task)
            print(f"\nTokens: {result['total_tokens']} | Time: {result['total_time']:.1f}s")
    else:
        print("Usage: python3 synergistic_harness.py 'task'")
        print("       python3 synergistic_harness.py --interactive")

if __name__ == "__main__":
    main()
