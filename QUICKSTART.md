# Quickstart

This path proves the public component that exists today: deterministic
pre-action policy checks. It does not execute an AI coding task or claim that
the framework is a production sandbox.

## Run

```bash
git clone https://github.com/prettybusysolutions-eng/adversarial-coder.git
cd adversarial-coder
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

Expected result: the test suite accepts a declared dependency install and
blocks force-pushing the default branch and piping remote code to a shell.

## Embed

```python
from security_monitor import SecurityMonitor

monitor = SecurityMonitor()
decision = monitor.evaluate("git push --force origin main")
assert decision.should_block
print(decision.reason)
```

The output is a policy decision, not proof that an external command was
intercepted. The caller remains responsible for enforcing the decision before
execution.
