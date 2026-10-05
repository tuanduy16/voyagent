import os

from dotenv import load_dotenv

load_dotenv()

# "api": Claude API with ANTHROPIC_API_KEY (pay-as-you-go)
# "subscription": runs each agent through the Claude Code CLI (`claude -p`) on your
#                 Claude Pro/Max login; counts against your plan's usage limits.
BACKEND = os.getenv("VOYAGENT_BACKEND", "api")
CLAUDE_CLI = os.getenv("VOYAGENT_CLAUDE_PATH", "claude")
CLI_TIMEOUT_SECONDS = int(os.getenv("VOYAGENT_CLI_TIMEOUT", "900"))

MODEL = os.getenv("VOYAGENT_MODEL", "claude-opus-5-5")
SPECIALIST_MODEL = os.getenv("VOYAGENT_SPECIALIST_MODEL", MODEL)
ORCHESTRATOR_EFFORT = os.getenv("VOYAGENT_ORCHESTRATOR_EFFORT", "high")
SPECIALIST_EFFORT = os.getenv("VOYAGENT_SPECIALIST_EFFORT", "medium")

# Server-side refusal fallbacks (re-runs a declined request on another model).
USE_FALLBACKS = os.getenv("VOYAGENT_FALLBACKS", "1") != "0"

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY") or None

# Wikimedia rejects requests without a contact URL or email in the User-Agent.
HTTP_USER_AGENT = os.getenv(
    "VOYAGENT_USER_AGENT",
    "Voyagent/0.1 (https://github.com/tuanduy16/voyagent) python-httpx",
)
