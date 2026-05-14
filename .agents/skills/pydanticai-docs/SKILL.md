---
name: PydanticAI Docs
description: Full PydanticAI documentation for API reference — agents, tools, messages, streaming events, output types
triggers:
  - "PydanticAI API"
  - "AgentStreamEvent"
  - "FunctionToolCallEvent"
  - "run_stream_events"
  - "PartStartEvent"
  - "PartDeltaEvent"
  - "TextPartDelta"
  - "ThinkingPartDelta"
  - "ToolCallPartDelta"
  - "pydantic_ai.messages"
  - "AgentRunResult"
  - "StreamedRunResult"
  - "AgentRunResultEvent"
  - "FunctionToolResultEvent"
  - "pydantic-ai agent"
  - "pydanticai docs"
---

# PydanticAI Documentation

Full API reference and guides for PydanticAI, located at `docs/pydanticai-llms-full.txt` in the project root.

## Usage

When you need to look up a PydanticAI API, class, method, property, or event type:

1. **Search the docs file**: Use grep on `docs/pydanticai-llms-full.txt` to find relevant sections
2. **Read focused sections**: Don't load the entire file — it's ~3MB. Read specific offset/limit ranges around your search results

Example:
```bash
rg "FunctionToolCallEvent" docs/pydanticai-llms-full.txt    # find event class
rg "run_stream_events" docs/pydanticai-llms-full.txt -A 20  # find method with context
```

## Key Topics Covered

- Agent creation and configuration
- `run()`, `run_sync()`, `run_stream()`, `run_stream_events()`, `iter()`
- `AgentStreamEvent` and all subclasses (`PartStartEvent`, `PartDeltaEvent`, `PartEndEvent`, `FunctionToolCallEvent`, `FunctionToolResultEvent`, `FinalResultEvent`, `AgentRunResultEvent`)
- `ModelResponsePartDelta` subtypes (`TextPartDelta`, `ThinkingPartDelta`, `ToolCallPartDelta`)
- Function tools, toolsets, deferred tools
- Output types (structured, tool output, streaming)
- Capabilities, hooks, dependencies
- Messages and chat history
- Model configuration and providers
- Testing patterns
