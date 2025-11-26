# Gemini / Antigravity Guidelines for nf_ws

This document outlines the specific operational rules for AI agents (Gemini/Antigravity) working on the `nf_ws` (nf_loto_platform) project.

## 1. Communication Style

- **Language**: All responses, questions, and confirmations must be in **Japanese**.
- **Tone**: Polite, professional, and helpful.
- **Structure**:
    1.  **Goal**: Briefly state the objective.
    2.  **Prerequisites**: Clarify assumptions and current understanding.
    3.  **Execution Steps**: Provide specific, numbered steps for local reproduction.
    4.  **Proposed Changes**: Show diffs or code snippets with clear rationale.
    5.  **Questions**: Ask specific questions if information is missing.
- **Explanation**: Explain technical concepts clearly in Japanese. Do not just dump code; explain the *why* and *how*.

## 2. Safety & Operational Boundaries

- **Database (PostgreSQL)**:
    -   Assume all DB operations are for a **local development environment**.
    -   **NEVER** execute destructive commands (`DROP`, `TRUNCATE`, bulk `DELETE`) without explicit user permission.
    -   **NEVER** assume access to production databases.
- **File System**:
    -   Only modify files within the active workspace.
    -   Do not touch system directories or files outside the project root unless necessary for environment setup (and with permission).
- **Long-running Tasks**:
    -   For heavy training or batch jobs, propose a **dry-run** or a small-scale test first.

## 3. Project Structure Awareness

- **Root**: `/mnt/e/env/ts/nf/nf_ws` (User's environment)
- **Package**: `src/nf_loto_platform`
- **Entry Points**:
    -   WebUI: `apps/webui_streamlit/streamlit_app.py`
    -   CLI: `apps/webui_streamlit/nf_auto_runner_full.py` (Legacy), `src/nf_loto_platform/pipelines/easytsf_runner.py` (New/Fixing)
- **Tests**: `tests/` (Mirrors `src` structure)

## 4. Agent Behavior

- **Proactive**: Identify potential issues (like missing entry points) and propose fixes.
- **Transparent**: Clearly state what you are doing and why.
- **Documentation**: Keep `AGENTS.md` and `GEMINI.md` updated as the project evolves.
