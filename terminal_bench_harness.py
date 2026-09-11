#!/usr/bin/env python3
"""
Terminal Bench Harness v1.0
===========================

Specialized harness for Terminal Bench-style tasks.
Focuses on precision tool calling, multi-step reasoning, and error recovery.
"""

import json
import time
import urllib.request
import subprocess
import os
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

@dataclass
class ToolCall:
    name: str
    arguments: Dict
    confidence: float

@dataclass
class StepResult:
    step: int
    tool_call: Optional[ToolCall]
    output: str
    success: bool
    error: Optional[str]
    tokens: int
    time: float

@dataclass
class TaskResult:
    task_id: str
    instruction: str
    steps: List[StepResult]
    final_state: Dict
    success: bool
    total_tokens: int
    total_time: float

class TerminalBenchHarness:
    """
    Harness optimized for Terminal Bench-style tasks.
    
    Key features:
    1. Precision tool calling with validation
    2. Multi-step planning and execution
    3. Error detection and recovery
    4. State tracking and verification
    """
    
    def __init__(self, executor_port: int = 1235, reasoner_port: int = 1234):
        self.executor_url = f"http://localhost:{executor_port}"
        self.reasoner_url = f"http://localhost:{reasoner_port}"
        
        # Enhanced system prompts
        self.executor_prompt = """You are a precision tool-calling assistant for terminal tasks.

CRITICAL RULES:
1. Always use the exact tool format: <function name="tool_name"><param name="param_name">value</param></function>
2. Validate your output before responding
3. If a command fails, analyze the error and try a fix
4. Track state changes between steps
5. Be concise - output only the tool call

Available tools:
- bash: Execute shell commands
  <function name="bash"><param name="command">your_command</param></function>
  
- read_file: Read file contents
  <function name="read_file"><param name="path">/path/to/file</param></function>
  
- write_file: Write to a file
  <function name="write_file"><param name="path">/path/to/file</param><param name="content">file content</param></function>
  
- search_files: Find files
  <function name="search_files"><param name="pattern">*.py</param><param name="path">/search/dir</param></function>
  
- edit_file: Edit a file
  <function name="edit_file"><param name="path">/path/to/file</param><param name="old_text">text to replace</param><param name="new_text">replacement text</param></function>
  
- check_state: Verify current state
  <function name="check_state"><param name="what">files|processes|env</param></function>

Never explain what you're going to do. Just do it."""

        self.reasoner_prompt = """You are a planning and reasoning assistant for terminal tasks.

Your job is to:
1. Analyze the instruction
2. Create a step-by-step plan
3. Verify the final state
4. Suggest error recovery if needed

Respond in JSON format:
{
    "analysis": "Brief analysis of the task",
    "steps": [
        {"action": "description", "tool": "bash|read_file|write_file", "params": {...}, "validation": "how to verify"}
    ],
    "success_criteria": ["criteria1", "criteria2"],
    "potential_errors": ["error1", "error2"],
    "recovery_plan": "what to do if errors occur"
}"""
    
    def _call_model(self, url: str, messages: List[Dict], max_tokens: int = 500) -> Dict:
        """Call a model endpoint."""
        body = {
            "model": "default",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1
        }
        
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
                "tokens": result["usage"]["completion_tokens"],
                "time": time.time() - start
            }
        except Exception as e:
            return {
                "content": f"Error: {e}",
                "reasoning": "",
                "tokens": 0,
                "time": time.time() - start
            }
    
    def _extract_tool_call(self, content: str) -> Optional[ToolCall]:
        """Extract tool call from XML output with validation."""
        if "<function" not in content:
            return None
        
        try:
            # Extract function name
            name_match = re.search(r'<function\s+name="([^"]+)"', content)
            if not name_match:
                return None
            name = name_match.group(1)
            
            # Extract parameters
            params = {}
            param_pattern = r'<param\s+name="([^"]+)">(.*?)</param>'
            for match in re.finditer(param_pattern, content, re.DOTALL):
                param_name = match.group(1)
                param_value = match.group(2).strip()
                params[param_name] = param_value
            
            # Calculate confidence based on completeness
            required_params = {
                "bash": ["command"],
                "read_file": ["path"],
                "write_file": ["path", "content"],
                "search_files": ["pattern"],
                "edit_file": ["path", "old_text", "new_text"],
                "check_state": ["what"]
            }
            
            if name in required_params:
                missing = [p for p in required_params[name] if p not in params]
                confidence = 1.0 - (len(missing) * 0.2)
            else:
                confidence = 0.5
            
            return ToolCall(
                name=name,
                arguments=params,
                confidence=max(0.0, confidence)
            )
        except Exception as e:
            return None
    
    def _execute_tool(self, tool_call: ToolCall) -> Tuple[str, bool]:
        """Execute a tool call and return output and success status."""
        try:
            if tool_call.name == "bash":
                cmd = tool_call.arguments.get("command", "")
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, 
                    timeout=30, cwd=os.path.expanduser("~")
                )
                output = result.stdout + result.stderr
                success = result.returncode == 0
                
            elif tool_call.name == "read_file":
                path = os.path.expanduser(tool_call.arguments.get("path", ""))
                with open(path, 'r') as f:
                    output = f.read()
                success = True
                
            elif tool_call.name == "write_file":
                path = os.path.expanduser(tool_call.arguments.get("path", ""))
                content = tool_call.arguments.get("content", "")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w') as f:
                    f.write(content)
                output = f"File written: {path}"
                success = True
                
            elif tool_call.name == "search_files":
                pattern = tool_call.arguments.get("pattern", "*")
                path = os.path.expanduser(tool_call.arguments.get("path", "."))
                result = subprocess.run(
                    f"find {path} -name '{pattern}' -type f",
                    shell=True, capture_output=True, text=True, timeout=10
                )
                output = result.stdout
                success = result.returncode == 0
                
            elif tool_call.name == "edit_file":
                path = os.path.expanduser(tool_call.arguments.get("path", ""))
                old_text = tool_call.arguments.get("old_text", "")
                new_text = tool_call.arguments.get("new_text", "")
                
                with open(path, 'r') as f:
                    content = f.read()
                
                if old_text in content:
                    content = content.replace(old_text, new_text, 1)
                    with open(path, 'w') as f:
                        f.write(content)
                    output = f"File edited: {path}"
                    success = True
                else:
                    output = f"Text not found in {path}"
                    success = False
                    
            elif tool_call.name == "check_state":
                what = tool_call.arguments.get("what", "files")
                if what == "files":
                    result = subprocess.run("ls -la", shell=True, capture_output=True, text=True)
                    output = result.stdout
                elif what == "processes":
                    result = subprocess.run("ps aux", shell=True, capture_output=True, text=True)
                    output = result.stdout
                elif what == "env":
                    output = json.dumps(dict(os.environ), indent=2)
                else:
                    output = f"Unknown state: {what}"
                success = True
            else:
                output = f"Unknown tool: {tool_call.name}"
                success = False
                
        except subprocess.TimeoutExpired:
            output = "Command timed out"
            success = False
        except Exception as e:
            output = f"Error: {e}"
            success = False
        
        return output, success
    
    def plan(self, instruction: str) -> Dict:
        """Phase 1: Create a plan using the reasoner."""
        messages = [
            {"role": "system", "content": self.reasoner_prompt},
            {"role": "user", "content": instruction}
        ]
        
        result = self._call_model(self.reasoner_url, messages, max_tokens=800)
        
        try:
            # Try to parse JSON from response
            json_match = re.search(r'\{.*\}', result['content'], re.DOTALL)
            if json_match:
                plan = json.loads(json_match.group())
                return plan
        except:
            pass
        
        # Fallback: create simple plan
        return {
            "analysis": instruction,
            "steps": [{"action": instruction, "tool": "bash", "params": {}, "validation": "check output"}],
            "success_criteria": ["task completed"],
            "potential_errors": ["command not found", "permission denied"],
            "recovery_plan": "try alternative approach"
        }
    
    def execute(self, plan: Dict) -> List[StepResult]:
        """Phase 2: Execute the plan step by step."""
        steps = []
        context = ""
        
        for i, step in enumerate(plan.get("steps", [])):
            action = step.get("action", "")
            tool = step.get("tool", "bash")
            params = step.get("params", {})
            
            # Build prompt with context
            prompt = f"Step {i+1}: {action}\n\nContext:\n{context}\n\nExecute this step:"
            
            messages = [
                {"role": "system", "content": self.executor_prompt},
                {"role": "user", "content": prompt}
            ]
            
            result = self._call_model(self.executor_url, messages, max_tokens=300)
            
            # Extract and execute tool call
            tool_call = self._extract_tool_call(result['content'])
            
            if tool_call:
                output, success = self._execute_tool(tool_call)
                context += f"\nStep {i+1}: {output[:500]}"
                
                steps.append(StepResult(
                    step=i+1,
                    tool_call=tool_call,
                    output=output[:1000],
                    success=success,
                    error=None if success else output[:200],
                    tokens=result['tokens'],
                    time=result['time']
                ))
            else:
                # No tool call - might be final answer
                steps.append(StepResult(
                    step=i+1,
                    tool_call=None,
                    output=result['content'][:1000],
                    success=True,
                    error=None,
                    tokens=result['tokens'],
                    time=result['time']
                ))
        
        return steps
    
    def verify(self, instruction: str, steps: List[StepResult], criteria: List[str]) -> Dict:
        """Phase 3: Verify the final state."""
        verification_prompt = f"""Instruction: {instruction}

Steps executed:
{chr(10).join([f"{s.step}. {'SUCCESS' if s.success else 'FAILED'}: {s.output[:100]}" for s in steps])}

Success criteria:
{chr(10).join([f"- {c}" for c in criteria])}

Verify if all criteria are met. Respond with JSON:
{{
    "verified": true/false,
    "criteria_met": ["met1", "met2"],
    "criteria_failed": ["failed1"],
    "suggestions": ["suggestion1"]
}}"""
        
        messages = [
            {"role": "system", "content": "You are a verification assistant. Check if the task was completed successfully."},
            {"role": "user", "content": verification_prompt}
        ]
        
        result = self._call_model(self.reasoner_url, messages, max_tokens=400)
        
        try:
            json_match = re.search(r'\{.*\}', result['content'], re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except:
            pass
        
        return {
            "verified": all(s.success for s in steps),
            "criteria_met": criteria if all(s.success for s in steps) else [],
            "criteria_failed": [] if all(s.success for s in steps) else ["incomplete"],
            "suggestions": []
        }
    
    def run(self, task_id: str, instruction: str) -> TaskResult:
        """Execute a complete terminal bench task."""
        print(f"\n{'='*60}")
        print(f"TASK: {task_id}")
        print(f"{'='*60}")
        print(f"Instruction: {instruction}\n")
        
        # Phase 1: Plan
        print("[1] Planning...")
        plan = self.plan(instruction)
        print(f"    Steps: {len(plan.get('steps', []))}")
        
        # Phase 2: Execute
        print("[2] Executing...")
        steps = self.execute(plan)
        
        for step in steps:
            status = "✓" if step.success else "✗"
            print(f"    {status} Step {step.step}: {step.output[:50]}...")
        
        # Phase 3: Verify
        print("[3] Verifying...")
        verification = self.verify(instruction, steps, plan.get("success_criteria", []))
        print(f"    Verified: {verification.get('verified', False)}")
        
        # Calculate totals
        total_tokens = sum(s.tokens for s in steps)
        total_time = sum(s.time for s in steps)
        success = verification.get("verified", False)
        
        print(f"\n{'='*60}")
        print(f"RESULT: {'PASS' if success else 'FAIL'}")
        print(f"Tokens: {total_tokens} | Time: {total_time:.1f}s")
        print(f"{'='*60}\n")
        
        return TaskResult(
            task_id=task_id,
            instruction=instruction,
            steps=steps,
            final_state=verification,
            success=success,
            total_tokens=total_tokens,
            total_time=total_time
        )

# Terminal Bench-style tasks
TERMINAL_BENCH_TASKS = [
    {
        "id": "tb-001",
        "instruction": "Create a Python script that reads a CSV file and outputs the mean of a numeric column",
        "criteria": ["script exists", "script runs", "correct output"]
    },
    {
        "id": "tb-002", 
        "instruction": "Set up a simple HTTP server that serves files from the current directory on port 8080",
        "criteria": ["server starts", "responds to requests"]
    },
    {
        "id": "tb-003",
        "instruction": "Find all Python files larger than 100KB and list them with their sizes",
        "criteria": ["command runs", "outputs file list"]
    },
    {
        "id": "tb-004",
        "instruction": "Create a backup of the home directory excluding .git folders",
        "criteria": ["backup created", ".git excluded"]
    },
    {
        "id": "tb-005",
        "instruction": "Write a bash script that monitors CPU usage and logs it to a file",
        "criteria": ["script exists", "script is executable", "logging works"]
    },
]

if __name__ == "__main__":
    harness = TerminalBenchHarness()
    
    # Run a test task
    task = TERMINAL_BENCH_TASKS[0]
    result = harness.run(task["id"], task["instruction"])
    
    print("\nFinal Results:")
    print(json.dumps(asdict(result), indent=2, default=str))
