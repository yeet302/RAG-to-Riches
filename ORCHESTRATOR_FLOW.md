# Keyword and Azure routing flow

[Open the standalone diagram](ORCHESTRATOR_FLOW.svg).

This shows the current implementation. Azure can generate question-specific JSON
assignments; Python validates and executes them. Answer generation is independent.

```mermaid
flowchart TD
    Frontend["Frontend: submit question"] --> API["main.py: ask()<br/>HTTP POST with JSON; validate input"]
    CLI["Terminal: chat.py"] --> Investigate["orchestrator.py: investigate()"]
    API --> Investigate
    Investigate --> Analyze["query_analysis.py: analyze_question()"]
    Analyze --> Mode{"Selected analysis mode"}

    Mode -->|Keywords| Keywords["router.py: classify()<br/>Match predefined words and phrases"]
    Keywords --> KeywordPlan["Return intent, reason, and predefined stages<br/>No model request"]
    KeywordPlan --> Result["QueryAnalysis returned to orchestrator.py"]

    Mode -->|Azure AI| Config{"Configured and outside canned demo mode?"}
    Config -->|Yes| Azure["Azure Foundry model<br/>Return intent, summary, reason, and JSON assignments"]
    Azure --> Validate{"JSON plan valid and bounded?"}
    Validate -->|Yes| Map["query_analysis.py: ModelAnalysis<br/>Validate roles, questions, tasks, and stages"]
    Map --> Result
    Config -->|No| Fallback["Use keyword result<br/>source: keyword_fallback + warning"]
    Validate -->|No| Fallback
    Azure -.->|Request failure or timeout| Fallback
    Fallback --> Result

    Result --> Plan["orchestrator.py: build RoutePlan"]
    Plan --> First["call_agent() + agents.py<br/>Send JSON questions/tasks to first-stage roles concurrently<br/>Validate returned findings and handle failures"]
    First --> NeedCode{"Second stage selected?"}
    NeedCode -->|Yes| Bundle["merge_findings()<br/>Combine first-stage evidence and statuses"]
    Bundle --> Code["call_agent() + agents.py<br/>Send JSON assignments with earlier evidence"]
    Code --> Combine["synthesize()<br/>Deduplicate evidence and prepare formatter fallback"]
    NeedCode -->|No| Combine
    Combine --> AnswerMode{"Answer generation"}
    AnswerMode -->|Evidence list| Return["Return response<br/>main.py: JSON to frontend<br/>chat.py: print to terminal"]
    AnswerMode -->|AI and supporting evidence| AnswerModel["answer_synthesis.py<br/>Azure writes from evidence; Python validates citations"]
    AnswerModel -->|Valid answer| Return
    AnswerModel -->|Unavailable or invalid| Formatter["Evidence-list fallback with visible warning"]
    Formatter --> Return
```

## Keyword routes and fallback

| Intent | First stage | Second stage |
|---|---|---|
| how_to | Docs | None |
| logs_lookup | Logs | None |
| code_lookup | Docs | Code |
| incident / unknown | Logs and docs together | Code |

- Keyword mode calls router.py through query_analysis.py and makes no Azure request for intent.
  Final answer generation can still use Azure; choose Evidence list to avoid that call.
- Azure mode validates generated assignments and executes their selected stages, which may differ from these keyword routes.
- The implementation precomputes the keyword result even in Azure mode, so it is
  ready as a fallback. Canned demo mode also bypasses Azure.
- In the default local configuration, docs/code search files and logs is unavailable.
  Actual teammate services and their databases are not connected.
- The input model interprets intent. A separate model call writes from collected evidence
  when AI explanation is selected (default when configured). Python validates citation IDs
  and retains the evidence-list fallback.

For the full implementation and demo checklist, see
[PIPELINE_SUMMARY.md](orchestrator/PIPELINE_SUMMARY.md).
