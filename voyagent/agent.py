"""Minimal agent runtime shared by the orchestrator and every specialist.

Each agent is a Claude tool-use loop with:
  * its own client-side tools (APIs we call),
  * optionally Claude's server-side web search,
  * a `submit_result` tool whose schema is the agent's Pydantic output model.
The loop ends when the agent submits a result that validates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from . import config

log = logging.getLogger("voyagent")

T = TypeVar("T", bound=BaseModel)

URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
# Models that accept `fallbacks="default"` on the Claude API.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
SUBMIT_TOOL = "submit_result"

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(max_retries=4)
    return _client


class AgentError(RuntimeError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[Any]]
    # Calls to run up front in subscription mode, where Claude Code can't call our tools.
    prefetch: list[dict] = field(default_factory=list)

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "eager_input_streaming": True,
        }


def inline_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema with every $ref inlined (tool schemas stay self-contained)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = resolve(defs[node["$ref"].split("/")[-1]])
                return {**target, **{k: resolve(v) for k, v in node.items() if k != "$ref"}}
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(n) for n in node]
        return node

    return resolve(schema)


def _norm(url: str) -> str:
    return url.strip().rstrip("/.,;").lower()


def _scrub_urls(obj: Any, seen: set[str]) -> None:
    """Null out any `source_url` the agent did not actually see in a tool result."""
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            value = getattr(obj, name)
            if name == "source_url" and isinstance(value, str) and _norm(value) not in seen:
                log.debug("dropping unverified url %s", value)
                setattr(obj, name, None)
            else:
                _scrub_urls(value, seen)
    elif isinstance(obj, list):
        for item in obj:
            _scrub_urls(item, seen)


class Agent(Generic[T]):
    def __init__(
        self,
        name: str,
        system: str,
        output_model: type[T],
        tools: list[Tool] | None = None,
        *,
        web_search: bool = False,
        max_web_searches: int = 5,
        model: str | None = None,
        effort: str | None = None,
        max_turns: int = 14,
        max_tokens: int = 32000,
    ):
        self.name = name
        self.system = system
        self.output_model = output_model
        self.tools = {t.name: t for t in tools or []}
        self.web_search = web_search
        self.max_web_searches = max_web_searches
        self.model = model or config.SPECIALIST_MODEL
        self.effort = effort or config.SPECIALIST_EFFORT
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.seen_urls: set[str] = set()

    # -- request -----------------------------------------------------------

    def _tool_definitions(self) -> list[dict]:
        defs = [t.definition() for t in self.tools.values()]
        defs.append(
            {
                "name": SUBMIT_TOOL,
                "description": (
                    f"Submit your final {self.name} result. Call this exactly once, when your research is "
                    "complete. If validation fails you will get the errors back and can resubmit."
                ),
                "input_schema": inline_schema(self.output_model),
                "eager_input_streaming": True,
            }
        )
        if self.web_search:
            defs.append({"type": "web_search_20260209", "name": "web_search", "max_uses": self.max_web_searches})
        return defs

    async def _call(self, messages: list[dict]):
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system,
            messages=messages,
            tools=self._tool_definitions(),
            cache_control={"type": "ephemeral"},
        )
        if not self.model.startswith("claude-haiku"):
            kwargs["output_config"] = {"effort": self.effort}
        if config.USE_FALLBACKS and self.model in FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        async with get_client().beta.messages.stream(**kwargs) as stream:
            return await stream.get_final_message()

    # -- tools -------------------------------------------------------------

    def _remember_urls(self, text: str) -> None:
        self.seen_urls.update(_norm(u) for u in URL_RE.findall(text))

    def _collect_server_urls(self, message) -> None:
        for block in message.content:
            if block.type == "web_search_tool_result" and isinstance(block.content, list):
                for result in block.content:
                    if url := getattr(result, "url", None):
                        self.seen_urls.add(_norm(url))
            elif block.type == "text":
                for citation in getattr(block, "citations", None) or []:
                    if url := getattr(citation, "url", None):
                        self.seen_urls.add(_norm(url))

    async def _run_tool(self, block) -> tuple[dict, T | None]:
        def result(content: str, is_error: bool = False) -> dict:
            return {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}

        if not isinstance(block.input, dict):
            return result("Tool input was not a JSON object. Try again.", True), None

        if block.name == SUBMIT_TOOL:
            try:
                parsed = self.output_model.model_validate(block.input)
            except ValidationError as e:
                log.info("[%s] submission failed validation, asking for a fix", self.name)
                return result(f"Validation failed:\n{e}\nFix these fields and call {SUBMIT_TOOL} again.", True), None
            return result("Result accepted."), parsed

        tool = self.tools.get(block.name)
        if tool is None:
            return result(f"Unknown tool '{block.name}'.", True), None
        log.info("[%s] -> %s(%s)", self.name, block.name, json.dumps(block.input, default=str)[:160])
        try:
            output = await tool.handler(**block.input)
        except Exception as e:  # tool failures are reported back to the agent, not raised
            log.warning("[%s] tool %s failed: %s", self.name, block.name, e)
            return result(f"Tool error ({type(e).__name__}): {e}", True), None
        text = output if isinstance(output, str) else json.dumps(output, default=str)
        self._remember_urls(text)
        return result(text), None

    # -- loop --------------------------------------------------------------

    async def run(self, task: str) -> T:
        log.info("[%s] started (%s, effort=%s, %s)", self.name, self.model, self.effort, config.BACKEND)
        self._remember_urls(task)
        if config.BACKEND == "subscription":
            return await self._run_cli(task)
        return await self._run_api(task)

    async def _run_api(self, task: str) -> T:
        messages: list[dict] = [{"role": "user", "content": task}]

        for _ in range(self.max_turns):
            message = await self._call(messages)
            self._collect_server_urls(message)

            if message.stop_reason == "refusal":
                details = getattr(message, "stop_details", None)
                raise AgentError(f"{self.name} agent request was declined: {details}")

            messages.append({"role": "assistant", "content": message.content})

            if message.stop_reason == "pause_turn":  # long server-side web search; resume it
                continue

            tool_uses = [b for b in message.content if b.type == "tool_use"]

            if message.stop_reason == "max_tokens":
                cut = "Your response hit the output limit and was cut off. Be more concise."
                results = [
                    {"type": "tool_result", "tool_use_id": b.id, "content": cut, "is_error": True}
                    for b in tool_uses
                ]
                messages.append({"role": "user", "content": results or cut})
                continue

            if not tool_uses:
                messages.append(
                    {"role": "user", "content": f"Please call {SUBMIT_TOOL} now with your final result."}
                )
                continue

            outcomes = await asyncio.gather(*(self._run_tool(b) for b in tool_uses))
            for _, parsed in outcomes:
                if parsed is not None:
                    _scrub_urls(parsed, self.seen_urls)
                    log.info("[%s] done", self.name)
                    return parsed
            messages.append({"role": "user", "content": [r for r, _ in outcomes]})

        raise AgentError(f"{self.name} agent did not submit a result within {self.max_turns} turns")

    # -- subscription backend (Claude Code CLI) ------------------------------

    async def _prefetched_data(self) -> str:
        """Run each tool's prefetch calls and format the results for the prompt."""
        calls = [(tool, args) for tool in self.tools.values() for args in tool.prefetch]
        if not calls:
            return ""

        async def one(tool: Tool, args: dict) -> str:
            log.info("[%s] prefetch %s(%s)", self.name, tool.name, json.dumps(args, default=str)[:120])
            try:
                out = await tool.handler(**args)
                text = out if isinstance(out, str) else json.dumps(out, default=str)
            except Exception as e:
                text = f"ERROR ({type(e).__name__}): {e}"
            self._remember_urls(text)
            return f"### {tool.name}({json.dumps(args, default=str)})\n{text}"

        sections = await asyncio.gather(*(one(t, a) for t, a in calls))
        return (
            "\n\nDATA ALREADY FETCHED FOR YOU (the tools mentioned in your instructions were run in advance; "
            "use these results as tool results):\n\n" + "\n\n".join(sections)
        )

    def _cli_command(self) -> list[str]:
        exe = shutil.which(config.CLAUDE_CLI)
        if not exe:
            raise AgentError(
                f"Claude Code CLI '{config.CLAUDE_CLI}' not found on PATH. Install Claude Code and run `claude` "
                "once to log in, or set VOYAGENT_CLAUDE_PATH."
            )
        tools = "WebSearch" if self.web_search else ""
        cmd = [
            exe, "-p",
            "--output-format", "stream-json", "--verbose",
            "--json-schema", json.dumps(inline_schema(self.output_model)),
            "--system-prompt", self.system,
            "--model", self.model,
            "--no-session-persistence",
            "--strict-mcp-config",
            "--tools", tools,
        ]
        if tools:
            cmd += ["--allowedTools", tools]
        if not self.model.startswith("claude-haiku"):
            cmd += ["--effort", self.effort]
        return cmd

    async def _cli_once(self, prompt: str) -> dict:
        proc = await asyncio.create_subprocess_exec(
            *self._cli_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),  # keep project files/CLAUDE.md out of the agent's context
            limit=64 * 1024 * 1024,
        )
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        result: dict | None = None

        async def read_stdout() -> None:
            nonlocal result
            async for raw in proc.stdout:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = event.get("type")
                if kind == "user":  # tool results, including web search results
                    self._remember_urls(json.dumps(event))
                elif kind == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "tool_use" and block.get("name") == "WebSearch":
                            log.info("[%s] -> web search: %s", self.name, block.get("input", {}).get("query"))
                elif kind == "result":
                    result = event

        stderr_task = asyncio.create_task(proc.stderr.read())
        try:
            await asyncio.wait_for(read_stdout(), timeout=config.CLI_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            raise AgentError(f"{self.name} agent timed out after {config.CLI_TIMEOUT_SECONDS}s")
        stderr = (await stderr_task).decode("utf-8", "replace").strip()
        await proc.wait()

        if result is None:
            raise AgentError(f"{self.name} agent: Claude Code exited without a result. {stderr[:500]}")
        if result.get("is_error") or result.get("subtype") != "success":
            detail = result.get("result") or result.get("subtype") or stderr
            raise AgentError(f"{self.name} agent failed in Claude Code: {str(detail)[:500]}")
        return result

    async def _run_cli(self, task: str) -> T:
        prompt = task + await self._prefetched_data()
        for attempt in range(2):
            result = await self._cli_once(prompt)
            output = result.get("structured_output")
            if output is None:  # fall back to parsing the text answer
                try:
                    output = json.loads(result.get("result", ""))
                except json.JSONDecodeError:
                    output = {}
            try:
                parsed = self.output_model.model_validate(output)
            except ValidationError as e:
                if attempt == 1:
                    raise AgentError(f"{self.name} agent returned invalid output: {e}") from e
                log.info("[%s] output failed validation, retrying", self.name)
                prompt += f"\n\nA previous attempt returned invalid output:\n{e}\nReturn output that fixes this."
                continue
            _scrub_urls(parsed, self.seen_urls)
            log.info("[%s] done", self.name)
            return parsed
        raise AssertionError("unreachable")
