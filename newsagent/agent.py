"""The conversational agent over your news library.

Built with LangChain's create_agent (LangGraph under the hood), so it runs a
real tool-calling loop: it decides which of the tools in tools.py to call,
reads the results, and calls more if it needs to.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from .config import CONFIG
from .llm import build_model
from .tools import ALL_TOOLS

SYSTEM_PROMPT = """You are the reader's newspaper assistant. They do not have
time to read the paper, so you read it for them. Every article you can see has
already been extracted from a PDF, summarised, and tagged against the topics
they keep in topics.txt.

How to work:

- Start from the tools, never from memory. You have no knowledge of what is in
  their library until you look, and you must not answer news questions from
  your own training data. If the tools return nothing on a subject, say so
  plainly -- do not fill the gap.
- For open requests ("what should I read", "catch me up", "anything on my
  topics"), call get_digest. For specific subjects, call search_news. Get the
  exact topic spellings from list_my_topics before filtering by topic.
- Lead with their topics of interest. That is the whole point of the topic
  file: an article tagged with one of their topics matters more than one that
  is not.
- Answer in the terminal, so keep it scannable: short topic headings, one line
  per story, the substance on that line. Cite the page and the article id like
  '(p3, #42)' so they can pull the full text if they want it.
- Distinguish what the paper reported from what you infer. If the reader asks
  something the articles do not answer, say the articles do not cover it.
- Be brief. A digest of twelve stories should be readable in under a minute.
  Do not pad, do not repeat the summary in different words, and do not
  editorialise about the news.
- You cannot edit topics.txt. When they want to change their interests, show
  them the exact line to add or remove and tell them the file path."""


def build_agent(*, with_memory: bool = True):  # noqa: ANN201 - LangGraph graph type
    """Construct the agent. with_memory keeps chat history across turns."""
    model = build_model(CONFIG.model, max_tokens=8000)
    return create_agent(
        model,
        tools=ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver() if with_memory else None,
        name="newspaper-agent",
    )


def ask(question: str, *, thread_id: str = "cli") -> str:
    """One-shot question. Returns the agent's final text answer."""
    agent = build_agent(with_memory=False)
    result = agent.invoke(
        {"messages": [HumanMessage(content=question)]},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": 40},
    )
    return last_text(result)


def last_text(result: dict) -> str:
    """Pull the final assistant text out of an agent result."""
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                joined = "\n".join(p for p in parts if p.strip())
                if joined.strip():
                    return joined.strip()
    return "(no answer)"
