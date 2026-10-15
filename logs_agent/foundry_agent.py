import json
import os

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition, Tool
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from openai.types.responses.response_input_param import (
    FunctionCallOutput,
    ResponseInputParam,
)

from log_tools import (
    get_log_context,
    list_log_sources,
    search_logs,
)

load_dotenv()

SYSTEM_PROMPT = """
You are a software engineer investigating application logs.

You have tools that let you search log files and inspect surrounding
log lines.

Rules:

1. Always call list_log_sources first and use the exact filenames
   it returns. Never guess filenames.
2. Always inspect the logs before making claims about an incident.
3. Do not invent facts that are not present in the logs.
4. Search again if the first results are insufficient.
5. Use get_log_context when you need to understand events around
   a particular log line.
6. Cite the filename and line number for important evidence.
7. Clearly distinguish:
   - facts shown directly in the logs
   - hypotheses based on the logs
   - information that cannot be determined
8. If there is insufficient evidence, say so.
"""

AVAILABLE_FUNCTIONS = {
    "list_log_sources": list_log_sources,
    "search_logs": search_logs,
    "get_log_context": get_log_context,
}

MAX_TOOL_STEPS = 8

FINAL_ANSWER_PROMPT = (
    "You have reached the maximum number of investigation steps. "
    "Do not call any more tools. Based only on the log evidence "
    "already gathered above, give your best final answer. Cite "
    "filenames and line numbers where possible, and clearly say "
    "what remains uncertain."
)


def build_tools() -> list[Tool]:
    return [
        FunctionTool(
            name="list_log_sources",
            description=(
                "List the available log files and the number of "
                "lines in each."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            strict=False,
        ),
        FunctionTool(
            name="search_logs",
            description=(
                "Search application logs for keywords or identifiers "
                "such as error text, component names, or IDs."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Keywords, error text, component names, "
                            "IDs, etc."
                        ),
                    },
                    "source": {
                        "type": "string",
                        "description": (
                            "Optional exact filename. Leave blank to "
                            "search all logs."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum number of matching lines "
                            "(default 20)."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            strict=False,
        ),
        FunctionTool(
            name="get_log_context",
            description=(
                "Get surrounding log lines around one event so you "
                "can understand what happened before and after it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Exact log filename.",
                    },
                    "line_number": {
                        "type": "integer",
                        "description": "Target line number.",
                    },
                    "before": {
                        "type": "integer",
                        "description": (
                            "Number of lines before the event "
                            "(default 5)."
                        ),
                    },
                    "after": {
                        "type": "integer",
                        "description": (
                            "Number of lines after the event "
                            "(default 5)."
                        ),
                    },
                },
                "required": ["source", "line_number"],
                "additionalProperties": False,
            },
            strict=False,
        ),
    ]


def run_tool(name: str, arguments_json: str, step: int):
    arguments = json.loads(arguments_json) if arguments_json else {}

    print(f"\n[Agent step {step}]")
    print(f"Tool: {name}")
    print(f"Arguments: {arguments}")

    function = AVAILABLE_FUNCTIONS.get(name)

    if function is None:
        result = {"error": f"Unknown tool: {name}"}
    else:
        try:
            result = function(**arguments)
        except Exception as e:
            result = {"error": str(e)}

    printed = str(result)
    print(
        f"Returned: {printed[:500]}"
        f"{'...' if len(printed) > 500 else ''}"
    )
    return result


def collect_tool_outputs(response, step: int) -> ResponseInputParam:
    input_list: ResponseInputParam = []

    for item in response.output:
        if item.type != "function_call":
            continue

        result = run_tool(item.name, item.arguments, step)
        input_list.append(
            FunctionCallOutput(
                type="function_call_output",
                call_id=item.call_id,
                output=json.dumps(result),
            )
        )

    return input_list


def ask_agent(question: str) -> str:
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model = os.environ["MODEL_DEPLOYMENT_NAME"]
    agent_name = os.environ["FOUNDRY_AGENT_NAME"]

    if "/openai/" in endpoint:
        raise ValueError(
            "FOUNDRY_PROJECT_ENDPOINT looks like a Responses API URL. "
            "Use the project endpoint from Foundry Overview, e.g. "
            "https://<resource>.services.ai.azure.com/api/projects/<project>"
        )

    project = AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )
    openai = project.get_openai_client()

    agent = project.agents.create_version(
        agent_name=agent_name,
        definition=PromptAgentDefinition(
            model=model,
            instructions=SYSTEM_PROMPT,
            tools=build_tools(),
        ),
    )

    conversation = openai.conversations.create()
    agent_ref = {
        "agent_reference": {
            "name": agent.name,
            "type": "agent_reference",
        }
    }

    response = openai.responses.create(
        input=question,
        conversation=conversation.id,
        extra_body=agent_ref,
    )

    for step in range(1, MAX_TOOL_STEPS + 1):
        input_list = collect_tool_outputs(response, step)

        if not input_list:
            return response.output_text

        # On the last budgeted step, submit tool outputs with tools
        # disabled so Foundry must answer instead of opening new calls.
        is_last_step = step == MAX_TOOL_STEPS
        if is_last_step:
            print(
                f"\n[Agent] Reached max tool steps ({MAX_TOOL_STEPS}); "
                "requesting final answer..."
            )

        response = openai.responses.create(
            input=input_list,
            conversation=conversation.id,
            tool_choice="none" if is_last_step else "auto",
            extra_body=agent_ref,
        )

        if is_last_step:
            return response.output_text

    return response.output_text


if __name__ == "__main__":
    model = os.getenv("MODEL_DEPLOYMENT_NAME", "unknown-model")
    print(f"Foundry OpenStack Log Agent ({model})")
    print("Type 'quit' to exit.\n")

    while True:
        question = input("Question: ").strip()

        if question.lower() in {"quit", "exit"}:
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
