"""Builds the Haystack 3 Agent used by the virtual assistant."""

from __future__ import annotations

from datetime import date

from haystack.components.agents import Agent
from haystack.components.generators.chat import OpenAIResponsesChatGenerator
from haystack.components.generators.chat.types.protocol import ChatGenerator
from haystack.hooks.budget import TokenBudgetHook
from haystack.hooks.compaction import CompactionHook, SlidingWindowCompactor
from haystack.hooks.tool_result_offloading import (
    FileSystemToolResultStore,
    NeverOffload,
    OffloadOverChars,
    ToolResultOffloadHook,
)
from haystack.utils import Secret

from . import context
from .config import Settings
from .hooks import load_user_context, persist_turn
from .knowledge import KnowledgeBase
from .tools import build_tools

SYSTEM_PROMPT = """You are the European Commission virtual assistant, a demonstration prototype.
You help journalists, staff and citizens find information in European Commission press releases and speeches.

Rules:
- For any factual question about Commission announcements, call `search_press_releases` first and answer only
  from what it returns. Cite the reference (for example IP/25/1307) and the date for each fact.
- If the knowledge base does not contain the answer, say so plainly. Do not invent figures, names or dates.
- You have long-term memory of this user. Respect their stated preferences (language, format, length).
  If they refer to an earlier conversation and the context does not contain it, call `recall_memory`.
- If the user asks you to forget something specific, call `forget_memory` with the id shown in your context.
- You are an AI system, not a Commission official. Do not give legal advice. Keep answers concise and neutral.
- Today's date is {today}."""


def build_generator(settings: Settings) -> ChatGenerator:
    """gpt-5.1 on the AI@EC Platform through the Responses API (tool calling is not filtered there)."""
    return OpenAIResponsesChatGenerator(
        api_key=Secret.from_env_var("AIEC_API_KEY"),
        api_base_url=settings.aiec_base_url,
        model=settings.assistant_model,
        timeout=90,
        max_retries=2,
        generation_kwargs={
            "reasoning": {"effort": settings.assistant_reasoning_effort},
            # Stateless use of the Responses API: reasoning items are replayed from the encrypted payload
            # instead of being looked up by id on the provider side.
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "max_output_tokens": 2000,
        },
    )


def build_agent(settings: Settings, kb: KnowledgeBase, chat_generator: ChatGenerator | None = None) -> Agent:
    offload = ToolResultOffloadHook(
        store=FileSystemToolResultStore(root=settings.tool_results_dir),
        offload_strategies={
            # Answers must quote these results, so they always stay in context.
            ("search_press_releases", "recall_memory", "forget_memory"): NeverOffload(),
            # Safety net for any tool added later that returns bulky output.
            "*": OffloadOverChars(12000),
        },
    )
    return Agent(
        chat_generator=chat_generator or build_generator(settings),
        tools=build_tools(kb),
        system_prompt=SYSTEM_PROMPT.replace("{today}", date.today().isoformat()),
        max_agent_steps=settings.max_agent_steps,
        state_schema=context.STATE_SCHEMA,
        hooks={
            "before_run": [load_user_context],
            "before_llm": [
                TokenBudgetHook(max_total_tokens=settings.token_budget, add_final_message=True),
                CompactionHook(SlidingWindowCompactor(), context_window=128_000),
            ],
            "after_tool": [offload],
            "after_run": [persist_turn],
        },
    )
