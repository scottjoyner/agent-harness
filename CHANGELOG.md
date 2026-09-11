# Agent Harness Changelog

## Version 1.0.0 (2026-09-11)

### Initial Release

**Models:**
- MiniCPM5-2B LoRA (Port 1235): 10.5 tok/s, 8/8 tool calling
- VibeThinker-3B (Port 1234): 5.7 tok/s, reasoning only

**Features:**
- Dual-model synergistic harness
- Task routing based on request type
- systemd services for both models
- Basic benchmark suite

**Performance:**
- Tool calling: 100% accuracy (8/8)
- Average generation speed: 10.5 tok/s
- Average prompt speed: 23-33 tok/s

**Known Issues:**
- VibeThinker doesn't output tool calls directly
- Ling3.0-tiny too slow for CPU (1.5 tok/s)
- iGPU doesn't support 16-bit storage

---

## Roadmap

### Version 1.1.0 (Planned)
- [ ] Improve VibeThinker tool calling with better prompts
- [ ] Add multi-turn conversation support
- [ ] Implement feedback loop for self-improvement
- [ ] Add more edge case tests

### Version 1.2.0 (Planned)
- [ ] Create task-specific LoRAs
- [ ] Implement dynamic load balancing
- [ ] Add conversation history sharing
- [ ] Optimize memory usage

### Version 2.0.0 (Future)
- [ ] Multi-node distribution (OptiPlex + xwing)
- [ ] Automatic LoRA fine-tuning from traces
- [ ] Specialized model routing
- [ ] Production-ready error handling

---

## Benchmark History

### 2026-09-11: Initial Benchmark

| Model | Tool Calling | Speed | RAM |
|-------|--------------|-------|-----|
| MiniCPM5 LoRA | 8/8 (100%) | 10.5 tok/s | 2.8GB |
| VibeThinker | 0/8 (0%) | 5.7 tok/s | 1.5GB |
| Ling3.0-tiny | Not tested | 1.5 tok/s | 3.5GB |

**Hardware:** OptiPlex-9030-AIO (i5-4590S, 7.7GB RAM)

---

## Model Evaluation

### MiniCPM5-2B LoRA
- **Strengths:** Excellent tool calling, fast generation
- **Weaknesses:** Requires LoRA adapter for tool calling
- **Best for:** Tool execution, function calling
- **Status:** Production ready

### VibeThinker-3B
- **Strengths:** Good reasoning, smaller footprint
- **Weaknesses:** No native tool calling, slower
- **Best for:** Planning, evaluation, reasoning
- **Status:** Needs improvement for tool calling

### Ling3.0-tiny
- **Strengths:** Large model (7.9B), MoE architecture
- **Weaknesses:** Too slow on CPU, requires newer llama.cpp
- **Best for:** Complex reasoning (with GPU)
- **Status:** Not recommended for OptiPlex

---

## Testing Protocol

### Benchmark Categories
1. **Tool Calling:** bash, read, write, search operations
2. **Reasoning:** Explanation, comparison, debugging
3. **Multi-step:** Sequences, conditions, loops
4. **Edge Cases:** Empty, long, special characters, unicode

### Metrics
- **Success Rate:** % of tasks completed correctly
- **Generation Speed:** Tokens per second (output)
- **Prompt Speed:** Tokens per second (input)
- **Memory Usage:** RSS in MB

### Hardware Requirements
- **Minimum:** 4GB RAM, 4-core CPU
- **Recommended:** 8GB RAM, 8-core CPU
- **Optimal:** GPU with 8GB+ VRAM
