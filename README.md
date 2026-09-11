# Synergistic Agent Harness

## Overview

A dual-model agent system using VibeThinker-3B and MiniCPM5-2B LoRA working synergistically on the OptiPlex-9030-AIO.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    SYNERGISTIC AGENT                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐           ┌─────────────────┐         │
│  │  VibeThinker-3B │           │  MiniCPM5-2B    │         │
│  │  (Reasoning)    │           │  + LoRA         │         │
│  │  Port 1234      │           │  (Execution)    │         │
│  │                 │           │  Port 1235      │         │
│  └────────┬────────┘           └────────┬────────┘         │
│           │                              │                  │
│           │  ┌──────────────────────────┘                  │
│           │  │                                              │
│           ▼  ▼                                              │
│  ┌─────────────────┐                                       │
│  │   Task Router   │                                       │
│  │   - Planning    │                                       │
│  │   - Execution   │                                       │
│  │   - Evaluation  │                                       │
│  └─────────────────┘                                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Model Roles

### VibeThinker-3B (Port 1234)
- **Role**: Reasoning, planning, evaluation
- **Strengths**: Understanding context, creating plans, evaluating results
- **Output**: JSON plans, analysis, reasoning

### MiniCPM5-2B LoRA (Port 1235)
- **Role**: Tool execution, structured output
- **Strengths**: Tool calling, function execution, `<function>` XML output
- **Output**: Tool calls in `<function>` XML format

## How It Works

1. **Planning Phase**: VibeThinker analyzes the request and creates a step-by-step plan
2. **Execution Phase**: MiniCPM5 LoRA executes each step using tools
3. **Evaluation Phase**: VibeThinker evaluates results and decides if more steps are needed

## Usage

### Command Line
```bash
# Simple task
python3 synergistic_harness.py "List all Python files"

# Interactive mode
python3 synergistic_harness.py --interactive

# Health check
python3 router.py health
```

### As a Library
```python
from synergistic_harness import run_task

result = run_task("Check system memory usage")
print(result['results'])
```

## Performance

| Metric | Value |
|--------|-------|
| VibeThinker | 8.9 tok/s |
| MiniCPM5 LoRA | 10.5 tok/s |
| Combined latency | ~2-3s per step |
| Tool calling accuracy | 8/8 (100%) |

## Services

### Systemd Services
```bash
# MiniCPM5 LoRA (auto-start)
systemctl --user status minicpm5-lora

# VibeThinker (manual start)
systemctl --user status llama-headless
```

### Manual Start
```bash
# MiniCPM5 LoRA
~/.lmstudio/extensions/backends/llama.cpp-linux-x86_64-avx2-2.30.0/llama-server \
  -m ~/.lmstudio/models/openbmb/MiniCPM5-2B-GGUF/MiniCPM5-2B-Q4_K_M.gguf \
  --lora ~/.lmstudio/models/openbmb/MiniCPM5-2B-GGUF/MiniCPM5-2B-iter2-lora.gguf \
  --ctx-size 4096 --batch-size 1024 --port 1235 --host 0.0.0.0 -t 2

# VibeThinker
~/.lmstudio/extensions/backends/llama.cpp-linux-x86_64-avx2-2.30.0/llama-server \
  -m ~/.lmstudio/models/mradermacher/VibeThinker-3B-i1-GGUF/VibeThinker-3B.i1-Q4_K_S.gguf \
  --ctx-size 2048 --port 1234 --host 0.0.0.0 -t 2
```

## Self-Improvement

The models can improve over time through:

1. **Trace Collection**: Log successful task executions
2. **Dataset Creation**: Convert traces to training data
3. **LoRA Fine-tuning**: Train on xwing with new data
4. **Deployment**: Update adapters on OptiPlex

### Trace Format
```json
{
  "task": "List files in home directory",
  "plan": ["List files using ls command"],
  "execution": [
    {
      "tool": "bash",
      "command": "ls -la ~",
      "result": "total 36..."
    }
  ],
  "success": true,
  "tokens": 445,
  "time": 2.3
}
```

## Future Work

1. **Dynamic Load Balancing**: Adjust thread allocation based on load
2. **Context Sharing**: Share conversation history between models
3. **Specialized LoRAs**: Create task-specific adapters
4. **Feedback Loop**: Use execution results to improve planning
5. **Multi-Model Expansion**: Add more specialized models

## Memory Usage

| Component | RAM |
|-----------|-----|
| MiniCPM5 LoRA | 2.8GB |
| VibeThinker | 1.5GB |
| System | 2.4GB |
| **Total** | **6.7GB** / 7.7GB |

## Troubleshooting

### Model Not Responding
```bash
# Check service status
systemctl --user status minicpm5-lora
systemctl --user status llama-headless

# Check ports
lsof -i :1234
lsof -i :1235

# Restart service
systemctl --user restart minicpm5-lora
```

### High Memory Usage
```bash
# Check memory
free -h
ps aux --sort=-%mem | head -5

# Reduce context size
# Edit service file: --ctx-size 2048
```

### Tool Calls Not Working
- Ensure MiniCPM5 LoRA is running with the LoRA adapter
- Check that the `<function>` XML format is being used
- Verify the model is not in reasoning-only mode
