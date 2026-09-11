#!/usr/bin/env python3
"""
Dual-Model Agent Harness: VibeThinker + MiniCPM5 LoRA
=====================================================

Architecture:
- VibeThinker (3B): Reasoning, planning, context understanding
- MiniCPM5 LoRA (2.5B): Tool calling, function execution

The models work synergistically:
1. VibeThinker analyzes the request and creates a plan
2. MiniCPM5 LoRA executes the tool calls
3. VibeThinker evaluates results and decides next steps
4. Both models contribute to self-improvement through fine-tuning
"""

import json
import time
import urllib.request
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

class ModelRole(Enum):
    REASONER = "reasoner"  # VibeThinker
    EXECUTOR = "executor"  # MiniCPM5 LoRA

@dataclass
class ModelEndpoint:
    name: str
    port: int
    role: ModelRole
    url: str

@dataclass
class TaskResult:
    model: str
    content: str
    reasoning: str
    tool_calls: List[Dict]
    tokens: int
    time: float
    success: bool

class DualModelHarness:
    """
    Harness for synergistic VibeThinker + MiniCPM5 LoRA agent system.
    
    The harness orchestrates both models to handle complex agent tasks:
    - VibeThinker: Understanding, planning, evaluation
    - MiniCPM5 LoRA: Tool execution, structured output
    """
    
    def __init__(self):
        self.models = {
            ModelRole.REASONER: ModelEndpoint(
                name="VibeThinker-3B",
                port=1234,
                role=ModelRole.REASONER,
                url="http://localhost:1234"
            ),
            ModelRole.EXECUTOR: ModelEndpoint(
                name="MiniCPM5-2B-LoRA",
                port=1235,
                role=ModelRole.EXECUTOR,
                url="http://localhost:1235"
            )
        }
        
        # Tools available to the executor
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Execute a bash command",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "The bash command to execute"}
                        },
                        "required": ["command"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the file"}
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the file"},
                            "content": {"type": "string", "description": "Content to write"}
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_files",
                    "description": "Search for files matching a pattern",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Search pattern"},
                            "path": {"type": "string", "description": "Directory to search in"}
                        },
                        "required": ["pattern"]
                    }
                }
            }
        ]
    
    def _call_model(self, role: ModelRole, messages: List[Dict], 
                    tools: bool = False, max_tokens: int = 500) -> TaskResult:
        """Call a model endpoint and return structured result."""
        model = self.models[role]
        
        body = {
            "model": model.name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1
        }
        
        if tools and role == ModelRole.EXECUTOR:
            body["tools"] = self.tools
        
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{model.url}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        
        start = time.time()
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            result = json.loads(resp.read())
            elapsed = time.time() - start
            
            msg = result["choices"][0]["message"]
            return TaskResult(
                model=model.name,
                content=msg.get("content", ""),
                reasoning=msg.get("reasoning_content", ""),
                tool_calls=msg.get("tool_calls", []),
                tokens=result["usage"]["completion_tokens"],
                time=elapsed,
                success=True
            )
        except Exception as e:
            return TaskResult(
                model=model.name,
                content="",
                reasoning="",
                tool_calls=[],
                tokens=0,
                time=time.time() - start,
                success=False
            )
    
    def plan(self, user_request: str) -> str:
        """
        Phase 1: VibeThinker analyzes the request and creates a plan.
        """
        messages = [
            {"role": "system", "content": """You are a planning assistant. Analyze the user's request and create a step-by-step plan.
            
For each step, specify:
1. What action to take
2. Which tool to use (bash, read_file, write_file, search_files)
3. What parameters to pass

Format your response as a numbered list of steps."""},
            {"role": "user", "content": user_request}
        ]
        
        result = self._call_model(ModelRole.REASONER, messages, max_tokens=800)
        return result.content
    
    def execute_step(self, step: str, context: str = "") -> TaskResult:
        """
        Phase 2: MiniCPM5 LoRA executes a single step.
        """
        messages = [
            {"role": "system", "content": f"""You are a tool-calling assistant. Execute the given step using the appropriate tool.

Context from previous steps:
{context}

Always respond with a <function> tag when calling a tool."""},
            {"role": "user", "content": step}
        ]
        
        return self._call_model(ModelRole.EXECUTOR, messages, tools=True, max_tokens=300)
    
    def evaluate(self, user_request: str, plan: str, results: List[TaskResult]) -> str:
        """
        Phase 3: VibeThinker evaluates results and decides if we're done.
        """
        results_text = "\n".join([
            f"Step {i+1} ({r.model}): {'SUCCESS' if r.success else 'FAILED'}\n{r.content[:200]}"
            for i, r in enumerate(results)
        ])
        
        messages = [
            {"role": "system", "content": """You are an evaluation assistant. Review the execution results and determine:
1. Did we complete the user's request?
2. Are there any errors to fix?
3. Do we need additional steps?

Respond with either "COMPLETE" if done, or "CONTINUE" with next steps needed."""},
            {"role": "user", "content": f"""Original request: {user_request}

Plan:
{plan}

Execution results:
{results_text}"""}
        ]
        
        result = self._call_model(ModelRole.REASONER, messages, max_tokens=500)
        return result.content
    
    def run(self, user_request: str, max_iterations: int = 5) -> Dict:
        """
        Execute a complete agent task using both models synergistically.
        """
        print(f"\n{'='*60}")
        print(f"DUAL-MODEL AGENT TASK")
        print(f"{'='*60}")
        print(f"Request: {user_request}\n")
        
        # Phase 1: Planning
        print("Phase 1: Planning (VibeThinker)...")
        plan = self.plan(user_request)
        print(f"Plan:\n{plan}\n")
        
        # Phase 2: Execution
        print("Phase 2: Execution (MiniCPM5 LoRA)...")
        results = []
        context = ""
        
        # Parse plan into steps (simple split by numbers)
        steps = [s.strip() for s in plan.split('\n') if s.strip() and s.strip()[0].isdigit()]
        
        for i, step in enumerate(steps[:max_iterations]):
            print(f"\n  Executing step {i+1}: {step[:50]}...")
            result = self.execute_step(step, context)
            results.append(result)
            
            if result.success:
                context += f"\nStep {i+1}: {result.content[:200]}"
                print(f"  Result: {result.content[:100]}")
            else:
                print(f"  Failed: {result.content[:100]}")
        
        # Phase 3: Evaluation
        print("\nPhase 3: Evaluation (VibeThinker)...")
        evaluation = self.evaluate(user_request, plan, results)
        print(f"Evaluation: {evaluation[:200]}")
        
        return {
            "request": user_request,
            "plan": plan,
            "results": [
                {
                    "model": r.model,
                    "content": r.content,
                    "success": r.success,
                    "tokens": r.tokens,
                    "time": r.time
                }
                for r in results
            ],
            "evaluation": evaluation,
            "total_tokens": sum(r.tokens for r in results),
            "total_time": sum(r.time for r in results)
        }

def main():
    """Demo the dual-model harness."""
    harness = DualModelHarness()
    
    # Test tasks
    tasks = [
        "List all Python files in /home/scott and count them",
        "Check the system memory usage and report if it's high",
        "Create a backup of the routing.json file",
    ]
    
    for task in tasks:
        result = harness.run(task)
        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"Total tokens: {result['total_tokens']}")
        print(f"Total time: {result['total_time']:.1f}s")
        print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
