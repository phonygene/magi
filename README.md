# MAGI — Disagreement OS for LLMs

Three models. One decision.

Inspired by the MAGI supercomputer from Neon Genesis Evangelion.

## Install

```bash
pip install magi-system
```

## Quick Start

```bash
# Multi-model code review
magi diff --staged

# Ask a question with three perspectives
magi ask "What are the tradeoffs of microservices vs monolith?"

# Multi-model answer scoring
magi judge --question "What is 2+2?" --answer "4"
```

## How It Works

MAGI sends your query to three different LLMs (Claude, GPT, Gemini) in parallel. Each model analyzes independently from a different perspective. The system then produces a Decision Dossier containing:

- **Ruling** — the final answer
- **Confidence** — how much the models agreed
- **Minority Report** — dissenting opinions
- **Trace** — full decision history for replay and analysis

## Configuration

Set your API keys:

```bash
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
export GOOGLE_API_KEY=AI...
```

## License

MIT
