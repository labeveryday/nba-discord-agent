"""
Discord NBA bot powered by Strands Agents + your published MCP server: nba-stats-mcp.

How it works:
- Starts your MCP stdio server (nba-stats-mcp) and loads its tools via MCPClient
- Creates a Strands Agent that can call those tools
- Routes Discord messages that start with $nba to the agent

Env vars:
- DISCORD_TOKEN (required)
- OPENAI_API_KEY (optional; if set, uses OpenAI provider instead of default Bedrock)
- OPENAI_MODEL (optional; default: gpt-4o-mini)
- OPENAI_BASE_URL (optional; for OpenAI-compatible providers)
- NBA_MCP_USE_UVX (optional; "true" to run the MCP server via `uvx nba-stats-mcp`)
- NBA_MCP_COMMAND (optional; default: nba-stats-mcp)
- NBA_MCP_ARGS (optional; extra args appended, shlex-split)
"""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Optional

import discord
from dotenv import load_dotenv

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands_tools import current_time


SYSTEM_PROMPT = """You are an NBA analytics assistant inside Discord.

You have access to NBA data tools via MCP. Use tools whenever they help, and cite key numbers.

Guidelines:
- Be concise and Discord-friendly.
- If the user’s question is ambiguous (date, season, team, player), ask 1 clarifying question.
- Prefer factual answers over speculation.
- If the answer is long, provide a short summary first, then details.
- Keep responses concise by default; avoid dumping huge tables. Offer to continue in follow-ups.
- For subjective questions (e.g., “Who is better?”), give a brief, balanced opinion in <= 8 bullets and
  clearly label it as opinion.
"""

# Optional: handle Strands' max-tokens exception gracefully (import path can vary by version).
try:
    from strands.types.exceptions import MaxTokensReachedException  # type: ignore
except Exception:  # pragma: no cover
    MaxTokensReachedException = None  # type: ignore


def _is_max_tokens_exception(e: Exception) -> bool:
    """
    Detect Strands' MaxTokensReachedException even if we can't import the class.
    """
    if MaxTokensReachedException is not None and isinstance(e, MaxTokensReachedException):
        return True
    return type(e).__name__ == "MaxTokensReachedException"


def _is_openai_token_param_error(e: Exception) -> Optional[str]:
    """
    Detect OpenAI 400s where the model requires a different token limit parameter.

    Returns:
        "max_completion_tokens" or "max_tokens" if the error message indicates a switch, else None.
    """
    msg = str(e)
    if "Unsupported parameter: 'max_tokens'" in msg and "max_completion_tokens" in msg:
        return "max_completion_tokens"
    if "Unsupported parameter: 'max_completion_tokens'" in msg and "max_tokens" in msg:
        return "max_tokens"
    return None


def _is_openai_temperature_error(e: Exception) -> bool:
    """
    Detect OpenAI 400s where temperature is not supported (or only default=1 is supported).
    """
    msg = str(e)
    return (
        "Unsupported value: 'temperature'" in msg
        and "Only the default (1) value is supported" in msg
    )


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def build_mcp_client() -> MCPClient:
    """
    Create an MCP client wired to the nba-stats-mcp stdio server.
    """
    use_uvx = _truthy(os.getenv("NBA_MCP_USE_UVX"))
    extra_args = shlex.split(os.getenv("NBA_MCP_ARGS", ""))

    if use_uvx:
        command = "uvx"
        args = ["nba-stats-mcp", *extra_args]
    else:
        command = os.getenv("NBA_MCP_COMMAND", "nba-stats-mcp")
        args = [*extra_args]

    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command=command,
                args=args,
            )
        ),
        prefix="nba",
    )


def build_model():
    """
    Select a model provider.

    - If OPENAI_API_KEY is set, use Strands OpenAI provider.
    - Otherwise, use default Strands provider (Bedrock).
    """
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        # Explicitly configure Bedrock output limit to reduce MaxTokensReachedException risk.
        # Note: you can override via env vars if desired.
        bedrock_model_id = os.getenv("BEDROCK_MODEL_ID")
        bedrock_max_tokens = int(os.getenv("BEDROCK_MAX_TOKENS", "1500"))
        bedrock_temperature = float(os.getenv("BEDROCK_TEMPERATURE", "0.2"))

        return (
            BedrockModel(model_id=bedrock_model_id, max_tokens=bedrock_max_tokens, temperature=bedrock_temperature)
            if bedrock_model_id
            else BedrockModel(max_tokens=bedrock_max_tokens, temperature=bedrock_temperature)
        )

    # Optional dependency: strands-agents[openai]
    from strands.models.openai import OpenAIModel

    model_id = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.getenv("OPENAI_BASE_URL")
    openai_max_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "1500"))
    # Default to 1.0 because some models only support the default temperature.
    openai_temperature = float(os.getenv("OPENAI_TEMPERATURE", "1"))
    # Some OpenAI models accept `max_tokens`, others require `max_completion_tokens`.
    # If you hit a 400 complaining about max_tokens, set OPENAI_TOKEN_PARAM=max_completion_tokens.
    openai_token_param_raw = os.getenv("OPENAI_TOKEN_PARAM", "").strip()
    # Common misconfig: people put the number here (e.g., 2000). Treat it as OPENAI_MAX_TOKENS.
    if openai_token_param_raw.isdigit():
        openai_max_tokens = int(openai_token_param_raw)
        openai_token_param_raw = ""

    allowed_token_params = {"max_tokens", "max_completion_tokens"}
    openai_token_param = openai_token_param_raw
    if openai_token_param and openai_token_param not in allowed_token_params:
        # Ignore invalid value and fall back to heuristic.
        openai_token_param = ""

    if not openai_token_param:
        # Heuristic: OpenAI "o1/o3/o4" style models often want max_completion_tokens.
        lower = model_id.lower()
        openai_token_param = (
            "max_completion_tokens" if lower.startswith(("o1", "o3", "o4")) else "max_tokens"
        )

    client_args = {"api_key": openai_api_key}
    if base_url:
        client_args["base_url"] = base_url

    return OpenAIModel(
        client_args=client_args,
        model_id=model_id,
        params={
            # OpenAI-compatible parameter names:
            "temperature": openai_temperature,
            openai_token_param: openai_max_tokens,
        },
    )


def chunk_for_discord(text: str, limit: int = 1900) -> list[str]:
    """
    Discord hard limit is 2000 chars. Keep some slack for formatting.
    """
    text = (text or "").strip()
    if not text:
        return ["(no response)"]

    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    chunks.append(text)
    return chunks


def main() -> None:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Missing DISCORD_TOKEN in environment.")

    mcp_client = build_mcp_client()

    # Keep one Agent per conversation so @mentions and replies continue context.
    # Strands Agent is not concurrency-safe, and most MCP stdio servers aren't designed
    # for concurrent calls on a single connection either, so we serialize invocations.
    global_invoke_lock = asyncio.Lock()
    agents_by_conversation: dict[str, Agent] = {}

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        model = build_model()
        conversation_manager = SlidingWindowConversationManager(
            window_size=int(os.getenv("STRANDS_WINDOW_SIZE", "30")),
            # Per-turn management can help keep tool-heavy loops from blowing up context.
            per_turn=True,
        )

        def get_agent(conversation_id: str) -> Agent:
            """
            Create/reuse an Agent with its own conversation history.
            """
            existing = agents_by_conversation.get(conversation_id)
            if existing is not None:
                return existing

            new_agent = Agent(
                model=model,
                tools=tools + [current_time],
                system_prompt=SYSTEM_PROMPT,
                conversation_manager=conversation_manager,
                #callback_handler=None,  # keep stdout quiet; Discord is the UI
                name="NBA Discord Agent",
                description="Answers NBA questions using nba-stats-mcp tools.",
            )
            agents_by_conversation[conversation_id] = new_agent
            return new_agent

        intents = discord.Intents.default()
        intents.message_content = True

        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            print(f"Logged in as {client.user}")
            print("Try in Discord: $nba who won the Celtics game last night?")
            print("You can also @mention the bot or reply to it to continue the conversation.")

        @client.event
        async def on_message(message: discord.Message):
            if message.author == client.user:
                return

            content = (message.content or "").strip()
            if not content:
                return

            if client.user is None:
                return

            if content.startswith("$help"):
                await message.channel.send(
                    "Commands:\n"
                    "- `$nba <question>`: ask NBA questions (scores, standings, player/team stats, etc.)\n"
                    "- `@Bot <question>`: mention the bot to ask / continue\n"
                    "- Reply to the bot: continue the same conversation\n"
                    "- `$help`: show this message"
                )
                return

            # Triggers:
            # - $nba prefix
            # - bot is mentioned
            # - message is a reply to a bot-authored message
            is_nba_command = content.startswith("$nba")
            is_mention = client.user.mentioned_in(message)
            is_reply_to_bot = False
            if message.reference and message.reference.resolved:
                referenced = message.reference.resolved
                if isinstance(referenced, discord.Message) and referenced.author == client.user:
                    is_reply_to_bot = True

            if not (is_nba_command or is_mention or is_reply_to_bot):
                return

            # Conversation id:
            # - If in a thread, treat the whole thread as one conversation
            # - Else, scope to (channel, user) to avoid mixing different users in a busy channel
            if isinstance(message.channel, discord.Thread):
                conversation_id = f"thread:{message.channel.id}"
            else:
                conversation_id = f"channel:{message.channel.id}:user:{message.author.id}"

            agent = get_agent(conversation_id)

            # Extract question text depending on trigger.
            question = content
            if is_nba_command:
                question = content[len("$nba") :].strip()
            elif is_mention:
                question = content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "")
                question = question.strip(" :,-\n\t")
            if not question:
                await message.channel.send("Send a question after `$nba` or after the @mention.")
                return

            async with global_invoke_lock:
                try:
                    # Run the sync agent call off the Discord event loop thread.
                    try_question = question
                    result = await asyncio.to_thread(agent, try_question)
                    response_text = str(result).strip()
                except Exception as e:
                    # Handle OpenAI parameter mismatch (max_tokens vs max_completion_tokens) by switching
                    # the env var and rebuilding the Agent's model, then retry once.
                    token_param = _is_openai_token_param_error(e)
                    if token_param is not None:
                        os.environ["OPENAI_TOKEN_PARAM"] = token_param
                        # Rebuild the model and replace on this agent instance.
                        try:
                            agent.model = build_model()
                            result = await asyncio.to_thread(agent, question)
                            response_text = str(result).strip()
                        except Exception as e3:
                            response_text = f"Error while answering: {type(e3).__name__}: {e3}"
                    # Handle models that don't support temperature (or only support default=1).
                    elif _is_openai_temperature_error(e):
                        os.environ["OPENAI_TEMPERATURE"] = "1"
                        try:
                            agent.model = build_model()
                            result = await asyncio.to_thread(agent, question)
                            response_text = str(result).strip()
                        except Exception as e4:
                            response_text = f"Error while answering: {type(e4).__name__}: {e4}"
                    # If the agent hit its output token ceiling, retry with a stricter brevity instruction.
                    elif _is_max_tokens_exception(e):
                        brief = (
                            f"{question}\n\n"
                            "Respond VERY briefly (<= 8 bullet points). "
                            "If this is still too much, ask ONE clarifying question."
                        )
                        try:
                            result = await asyncio.to_thread(agent, brief)
                            response_text = str(result).strip()
                        except Exception as e2:
                            response_text = f"Error while answering: {type(e2).__name__}: {e2}"
                    else:
                        response_text = f"Error while answering: {type(e).__name__}: {e}"

            for part in chunk_for_discord(response_text):
                await message.channel.send(part)

        client.run(token)


if __name__ == "__main__":
    main()

