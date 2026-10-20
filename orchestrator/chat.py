"""Small terminal chatbot for the same pipeline used by POST /api/ask."""
from __future__ import annotations

import asyncio
from uuid import uuid4

from orchestrator import investigate


async def chat():
    conversation_id = str(uuid4())
    print("Incident Copilot. Type a question, or /quit to exit.")
    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question.lower() in {"/quit", "/exit"}:
            break
        if not question:
            continue
        if len(question) > 2000:
            print("Copilot: Please use at most 2000 characters.")
            continue
        response = await investigate(question, conversation_id=conversation_id)
        print(f"\nCopilot: {response.answer}")
        print(f"Agents: {', '.join(response.agents_used)} | request: {response.request_id}\n")


if __name__ == "__main__":
    asyncio.run(chat())
