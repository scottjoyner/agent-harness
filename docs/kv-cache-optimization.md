# KV Cache Sharing Optimization

## Overview

A custom KV cache handling system that leverages the synergy between MiniCPM5 LoRA (executor) and VibeThinker (reasoner) to improve throughput and reduce latency in the multi-agent harness.

## Architecture

### Current Flow
```
User Input → MiniCPM5 (generate) → Execute → VibeThinker (evaluate) → Output
             [300ms prompt]        [cmd]     [300ms prompt]
```

### Optimized Flow with KV Cache Sharing
```
User Input → MiniCPM5 (generate) ─┬─→ Execute ─┬─→ VibeThinker (evaluate) → Output
             [300ms prompt]        │   [cmd]    │   [50ms cached prompt]
                                   │            │
                                   └─→ Stream tokens to VibeThinker KV cache
```

## Implementation Plan

### Phase 1: Token Streaming Bridge (Current Focus)

**Goal:** While MiniCPM5 generates tokens, stream them to VibeThinker's prompt processing pipeline.

**Components:**

1. **Token Stream Manager**
   ```python
   class TokenStreamManager:
       def __init__(self, producer_model, consumer_model):
           self.producer = producer_model  # MiniCPM5
           self.consumer = consumer_model  # VibeThinker
           self.stream_buffer = asyncio.Queue()
           self.kv_cache = {}
       
       async def stream_tokens(self):
           """Stream generated tokens to consumer's KV cache."""
           async for token in self.producer.generate_stream():
               await self.stream_buffer.put(token)
               # Pre-process token for consumer model
               await self.consumer.prefill_token(token)
   ```

2. **KV Cache Pre-processor**
   ```python
   class KVCachePreprocessor:
       def __init__(self, model):
           self.model = model
           self.cache = {}
           self.position = 0
       
       async def prefill_token(self, token):
           """Process a single token and store in KV cache."""
           # Run model's forward pass on just this token
           # Store key/value states for later use
           k, v = self.model.forward_single(token, self.position)
           self.cache[self.position] = (k, v)
           self.position += 1
       
       def get_cached_prompt(self):
           """Return pre-computed KV states for cached tokens."""
           return self.cache
   ```

3. **Synergy-Aware Scheduler**
   ```python
   class SynergyScheduler:
       def __init__(self, executor, reasoner):
           self.executor = executor  # MiniCPM5 LoRA
           self.reasoner = reasoner  # VibeThinker
           self.stream_manager = TokenStreamManager(executor, reasoner)
       
       async def execute_with_prefill(self, instruction):
           # Start streaming in background
           stream_task = asyncio.create_task(
               self.stream_manager.stream_tokens()
           )
           
           # Execute task with MiniCPM5
           result = await self.executor.generate(instruction)
           
           # Wait for streaming to complete
           await stream_task
           
           # Evaluate with pre-filled VibeThinker
           evaluation = await self.reasoner.generate_with_cache(
               result, 
               self.stream_manager.kv_cache
           )
           
           return evaluation
   ```

### Phase 2: Shared KV Cache Pool

**Goal:** Maintain a pool of pre-computed KV states for common prompt prefixes.

**Components:**

1. **Prompt Prefix Cache**
   - Cache KV states for system prompts
   - Cache KV states for common instruction patterns
   - LRU eviction based on usage

2. **Cache Synchronization**
   - Sync KV states between models when prompt structure is similar
   - Handle position encoding differences
   - Manage cache invalidation

3. **Memory Management**
   - Shared memory region for KV states
   - Memory-mapped cache for large prompts
   - Automatic cleanup of stale entries

### Phase 3: Hardware-Accelerated KV Cache

**Goal:** Leverage GPU/NPU for KV cache operations.

**Components:**

1. **CUDA/Metal KV Cache Kernels**
   - Custom kernels for KV cache operations
   - Batch processing of token sequences
   - Parallel prefill for multiple positions

2. **Memory Bandwidth Optimization**
   - Optimize memory access patterns
   - Reduce memory copies between CPU/GPU
   - Use unified memory where available

3. **Cache Compression**
   - Quantize KV states to reduce memory
   - Delta encoding for similar positions
   - Adaptive compression based on available memory

## Performance Targets

### Current Performance
- MiniCPM5 LoRA: ~7.7 tok/s
- VibeThinker: ~5.7 tok/s
- Total latency per task: ~2-3s (prompt processing + generation)

### Target Performance
- MiniCPM5 LoRA: ~7.7 tok/s (unchanged)
- VibeThinker: ~5.7 tok/s (unchanged)
- **Prompt processing reduction: 300ms → 50ms (83% improvement)**
- Total latency per task: ~1.5s (33% improvement)

### Benchmark Impact
- Current: 8/8 tasks passed (100% on simple tasks)
- Target: Same accuracy, 33% faster execution

## Integration Points

### With Existing Harness

1. **synergistic_harness.py**
   - Add TokenStreamManager initialization
   - Modify execute_task() to use streaming
   - Add cache management to cleanup

2. **terminal_bench_harness.py**
   - Add prefill support for multi-step tasks
   - Cache prompt prefixes between tasks
   - Track cache hit rates

3. **fast_bench_v7.py**
   - Add timing metrics for cache operations
   - Compare cached vs uncached performance
   - Generate cache efficiency reports

### With Models

1. **MiniCPM5 LoRA (Port 1235)**
   - Enable streaming generation mode
   - Expose token-by-token generation API
   - Add cache state inspection

2. **VibeThinker (Port 1234)**
   - Add prefill API endpoint
   - Expose KV cache state
   - Support partial prompt processing

## Implementation Timeline

### Week 1: Token Streaming Bridge
- [ ] Implement TokenStreamManager
- [ ] Add streaming generation to MiniCPM5
- [ ] Add prefill API to VibeThinker
- [ ] Basic integration test

### Week 2: KV Cache Pre-processor
- [ ] Implement KVCachePreprocessor
- [ ] Add position encoding handling
- [ ] Cache synchronization between models
- [ ] Performance benchmarking

### Week 3: Synergy-Aware Scheduler
- [ ] Implement SynergyScheduler
- [ ] Integrate with existing harness
- [ ] Add cache hit rate tracking
- [ ] Full benchmark suite

### Week 4: Optimization & Testing
- [ ] Memory optimization
- [ ] Cache compression
- [ ] Edge case handling
- [ ] Documentation

## Success Metrics

1. **Latency Reduction**
   - Prompt processing: 300ms → 50ms (83% reduction)
   - Total task time: 2s → 1.5s (25% reduction)

2. **Cache Efficiency**
   - Cache hit rate: >80% for system prompts
   - Cache hit rate: >60% for instruction patterns
   - Memory usage: <500MB for cache

3. **Accuracy Maintenance**
   - Same accuracy as uncached execution
   - No degradation in tool calling
   - No degradation in reasoning

## Risk Mitigation

1. **Memory Pressure**
   - Monitor memory usage during cache operations
   - Implement automatic cache eviction
   - Use memory-mapped files for large caches

2. **Cache Invalidation**
   - Detect when cached states are stale
   - Implement proper cache invalidation
   - Handle model updates gracefully

3. **Complexity Management**
   - Start with simple implementation
   - Add complexity incrementally
   - Maintain backward compatibility

## Future Enhancements

1. **Multi-Model Cache Sharing**
   - Extend to more than 2 models
   - Dynamic cache allocation based on model needs
   - Cross-model cache synchronization

2. **Predictive Caching**
   - Predict which tokens will be needed
   - Pre-cache predicted prompt patterns
   - Adaptive caching based on task type

3. **Distributed KV Cache**
   - Share cache across multiple machines
   - Use RDMA for fast cache access
   - Implement cache coherence protocols
