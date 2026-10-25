# OpenAlgo Read-Only Compatibility Probe

`scripts/probe_openalgo.py` calls read-only REST endpoints and prints a JSON
report of response shapes. It never places, modifies, or cancels orders.

## Invocation

Set `OPENALGO_API_KEY` and `OPENALGO_API_HOST` in the process environment for
an intentional target, then run:

```bash
uv run python scripts/probe_openalgo.py --host http://127.0.0.1:5000
```

The script also supports python-dotenv loading. Inherited environment values
can override file configuration; verify the intended host and credentials
before invoking it. Avoid passing credentials via `--api-key`, since command
arguments can be visible to other processes.

By default it probes ping, intervals, funds, orderbook, tradebook, positionbook,
holdings, and analyzer. Optional flags:

- `--include-quotes`: add quotes and multiquotes.
- `--include-history`: add history for the local current date.
- `--symbol`, `--exchange`, `--interval`: select market data parameters.
- `--timeout`: set the per-request HTTP timeout in seconds.

## Output and Limitations

The report includes endpoint success flags, envelope keys, and limited samples.
Known credential keys are redacted, but samples can contain account or trading
information. Review and sanitize reports before sharing or committing them.
The process exits successfully even if individual endpoints report failures;
inspect the endpoint results. This is not websocket or order-lifecycle testing.
