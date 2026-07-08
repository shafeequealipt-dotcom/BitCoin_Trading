# Phase 1.7 — systemd Anatomy

Investigation content lives in **[`phase1_synthesis.md`](phase1_synthesis.md) Section 7**.

## What's covered there

- `trading-workers.service`: ExecStart, User=`inshadaliqbal786`, Restart=always, RestartSec=15
- `trading-mcp-sse.service`: ExecStart, same user, Restart=always, RestartSec=10
- Permission verification: user owns the services → can `systemctl restart` without sudo
- Phase 4.A use of `subprocess.Popen([...], start_new_session=True)` so the restart child survives parent termination

See `systemd/trading-workers.service`, `systemd/trading-mcp-sse.service`.
