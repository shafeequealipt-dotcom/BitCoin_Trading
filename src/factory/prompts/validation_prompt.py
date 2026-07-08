"""Prompts for strategy code validation."""

VALIDATION_SYSTEM_PROMPT = """You are reviewing generated trading strategy code for correctness and safety. Check for bugs, unsafe operations, and interface compliance."""

VALIDATION_PROMPT = """Review this generated strategy code:

```python
{code}
```

Check:
1. Does it inherit from BaseStrategy?
2. Are all required properties implemented?
3. Does scan() return RawSignal or None?
4. Does vote() return (str, float, str)?
5. Any dangerous operations (file I/O, network calls, exec)?
6. Any obvious logic bugs?

Respond with JSON: {{"valid": true/false, "issues": ["issue1", "issue2"]}}
"""
