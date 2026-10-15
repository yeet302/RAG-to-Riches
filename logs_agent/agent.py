import json

from ollama import chat

from log_tools import (
    list_log_sources,
    search_logs,
    get_log_context,
)


MODEL = "qwen3:4b"


SYSTEM_PROMPT = """
You are a software engineer investigating application logs.

You have tools that let you search log files and inspect surrounding
log lines.

Rules:

1. Always inspect the logs before making claims about an incident.
2. Do not invent facts that are not present in the logs.
3. Search again if the first results are insufficient.
4. Use get_log_context when you need to understand events around
   a particular log line.
5. Cite the filename and line number for important evidence.
6. Clearly distinguish:
   - facts shown directly in the logs
   - hypotheses based on the logs
   - information that cannot be determined
7. If there is insufficient evidence, say so.
"""


AVAILABLE_FUNCTIONS = {
    "list_log_sources": list_log_sources,
    "search_logs": search_logs,
    "get_log_context": get_log_context,
}

TOOLS = [
    list_log_sources,
    search_logs,
    get_log_context,
]

MAX_TOOL_STEPS = 8

FINAL_ANSWER_PROMPT = (
    "You have reached the maximum number of investigation steps. "
    "Do not call any more tools. Based only on the log evidence "
    "already gathered above, give your best final answer. Cite "
    "filenames and line numbers where possible, and clearly say "
    "what remains uncertain."
)


def ask_agent(question):

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": question,
        },
    ]

    # Let the agent make several investigation steps.
    for step in range(MAX_TOOL_STEPS):

        response = chat(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
        )

        messages.append(response.message)

        tool_calls = response.message.tool_calls

        # If no tools were called, the model is done.
        if not tool_calls:
            return response.message.content

        for call in tool_calls:

            tool_name = call.function.name
            arguments = call.function.arguments

            print(f"\n[Agent step {step + 1}]")
            print(f"Tool: {tool_name}")
            print(f"Arguments: {arguments}")

            function = AVAILABLE_FUNCTIONS.get(tool_name)

            if function is None:
                result = {
                    "error": f"Unknown tool: {tool_name}"
                }

            else:
                try:
                    result = function(**arguments)

                except Exception as e:
                    result = {
                        "error": str(e)
                    }

            print(
                f"Returned: "
                f"{str(result)[:500]}"
                f"{'...' if len(str(result)) > 500 else ''}"
            )

            messages.append({
                "role": "tool",
                "tool_name": tool_name,
                "content": json.dumps(result),
            })

    # Tool budget exhausted: force a final answer from evidence so far.
    print(
        f"\n[Agent] Reached max tool steps ({MAX_TOOL_STEPS}); "
        "requesting final answer..."
    )

    messages.append({
        "role": "user",
        "content": FINAL_ANSWER_PROMPT,
    })

    response = chat(
        model=MODEL,
        messages=messages,
    )

    return response.message.content


if __name__ == "__main__":

    print(f"Local OpenStack Log Agent ({MODEL})")
    print("Type 'quit' to exit.\n")

    while True:

        question = input("Question: ").strip()

        if question.lower() in {
            "quit",
            "exit",
        }:
            break

        if not question:
            continue

        try:
            answer = ask_agent(question)

            print("\nAnswer:")
            print(answer)
            print()

        except Exception as e:
            print(f"\nError: {e}\n")