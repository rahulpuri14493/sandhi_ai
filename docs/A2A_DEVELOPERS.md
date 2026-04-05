# A2A for Developers

The platform runs on **A2A protocol architecture**. All agent calls go through A2A internally. You do **not** run any adapter yourself.

## Does my model support A2A?

**A2A is a protocol for the API endpoint, not a property of the model.**

- The **model** (e.g. your fine-tuned LLM) does not “support” or “not support” A2A.
- The **endpoint** you register either speaks A2A natively or speaks **OpenAI-compatible** API. Either way, the platform handles it.

### What you do

| Your endpoint | In the marketplace |
|---------------|--------------------|
| **OpenAI-compatible** (e.g. `/v1/chat/completions`, fine-tuned model API) | Register your **actual API URL**. Leave **“My endpoint is A2A protocol compliant”** **unchecked**. The platform will call your endpoint via its internal A2A ↔ OpenAI adapter so the architecture stays A2A. |
| **Native A2A** (implements JSON-RPC 2.0 SendMessage) | Register your A2A endpoint URL. **Check** “My endpoint is A2A protocol compliant.” The platform will call it directly with A2A. |

So: if your endpoint is OpenAI-compatible (including fine-tuned models), you only need to register that endpoint and leave the A2A box unchecked. The platform runs the adapter and routes your traffic through it; you don’t run anything extra.

---

## How it works (architecture)

- When you register an **OpenAI-compatible** endpoint (A2A unchecked), the platform sends the request to its **internal A2A adapter** with your URL and key in the request metadata. The adapter calls your endpoint with the usual OpenAI-style payload and returns an A2A response to the platform. So every call is A2A from the platform’s point of view.
- When you register a **native A2A** endpoint (A2A checked), the platform calls your URL directly with A2A.

In both cases the system architecture is A2A; the adapter is a platform service, not something you deploy.

---

## Summary

- **OpenAI-compatible or fine-tuned model**: Register your API URL, leave A2A **unchecked**. Platform uses its adapter; you don’t run anything.
- **Native A2A endpoint**: Register your URL and **check** A2A. Platform calls you directly.

Adapter implementation (run by the platform): [tools/a2a_openai_adapter/](../tools/a2a_openai_adapter/).

---

## Platform architecture and scale (A2A, registry, message bus)

### Does the current design need a message bus or separate registry?

**No.** The existing design is efficient for the current product:

- **Registry:** The platform uses the **database (Agent table) + REST API** as the registry. Agents are discovered via `GET /api/agents` (list/filter) and `GET /api/agents/{id}/a2a-card` (A2A Agent Card). There is no separate A2A registry service; the DB and API are the source of truth.
- **Communication:** The platform **orchestrates** workflows. It holds the workflow (steps and assigned agents) and invokes agents **synchronously** per step: it calls each agent’s endpoint (direct A2A or via the A2A↔OpenAI adapter), waits for the response, then passes that output to the next step. Agents do not talk to each other directly; the platform is the central orchestrator. A **message bus** (e.g. Redis/RabbitMQ/Kafka) is **not required** for this model. It would only be needed if you wanted true peer-to-peer async messaging (agents publishing/subscribing to events or calling each other without the platform in the loop).
- **Sequential vs A2A:** Both work with the same design. “Sequential” vs “A2A” is a **collaboration style** (whether a step receives the previous step’s output). Transport is always A2A protocol (direct or via adapter); the platform still does one call per step and waits for the response.

### Will 200 agents cause issues?

**No.** Execution does not depend on total agent count:

- Each **job** uses only the agents in its **workflow** (typically 2–5). The platform does not broadcast to all 200 agents; it only invokes the agents in the current job’s steps.
- **Agent listing** (marketplace) can return many agents. The API supports optional **pagination** (`limit`, `offset`) so that with 200+ agents you can page results and avoid large responses. Use `GET /api/agents?limit=50&offset=0` (and optionally `X-Total-Count` or total in response) when you need to scale the list.

So: the existing design works for both A2A and sequential workflows, does not require a message bus or separate registry, and scales to 200 agents without issues when listing uses pagination.

---

## Task envelope & tool assignment (platform → agent JSON)

When a job step runs, the platform attaches a versioned **`sandhi_a2a_task`** object (plus `sandhi_trace` and `platform_a2a_schema`) to the JSON your agent receives. Mandatory fields, registry-based tool assignment, validation flags, and test pointers are documented in **[A2A task & assignment](A2A_TASK_AND_ASSIGNMENT.md)**.
