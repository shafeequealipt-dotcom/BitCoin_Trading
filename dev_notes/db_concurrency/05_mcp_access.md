# 05 — MCP Server Database Access

Target: `src/mcp/` — the MCP (Model Context Protocol) server exposing tools to Claude Code / claude.ai.

## 1. Files inspected

- `src/mcp/server.py` — main SSE/stdio server class.
- `src/mcp/tools/analysis_tools.py`
- `src/mcp/tools/memory_tools.py`
- `src/mcp/tools/system_tools.py`

## 2. Database access sites

| File | Line | Op | Notes |
|---|---|---|---|
| `src/mcp/server.py` | 57 | `await self.db.connect()` | Server boot — opens the shared DatabaseManager |
| `src/mcp/server.py` | 360 | `await self.db.disconnect()` | Server shutdown |
| `src/mcp/tools/system_tools.py` | 73, 76 | `await db.execute(...)` | System maintenance tools (operator-invoked) |

## 3. Pattern classification

The MCP server reuses the same `DatabaseManager` instance from `ServiceContainer`. All actual data reads go through services and repositories (covered in 02/04), not direct DM calls from MCP handlers.

| Pattern | Count |
|---|---|
| Connect/disconnect lifecycle | 2 (in server.py, not at request time) |
| POINT WRITE (system_tools) | 2 (operator-invoked maintenance only) |
| ANALYTICAL READ | 0 (delegated to services) |

## 4. Concurrency profile

MCP tools are invoked on-demand by external clients (Claude Code, claude.ai). They are NOT a periodic high-volume consumer like the workers. Peak load is one external Claude session calling a few tools in succession.

Reads inherit whatever performance the underlying service layer provides; the MCP server itself adds no concurrent DB pressure.

## 5. Implications for the refactor

- No MCP code changes in Phase 3.
- MCP-initiated reads benefit from the reader pool automatically (because they go through services that use the same `db`).
- The two `system_tools.py` writes are operator-invoked maintenance, not a hot path.

End of `05_mcp_access.md`.
