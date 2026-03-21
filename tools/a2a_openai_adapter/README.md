# A2A ↔ OpenAI-Compatible Adapter

The **platform runs this adapter** as part of the stack. All agent calls that target OpenAI-compatible endpoints go through it so the architecture is A2A everywhere. **Developers do not run it**—they register their OpenAI-compatible API URL and leave **"My endpoint is A2A protocol compliant"** unchecked.

---

## When sequential workflow runs vs when A2A (peer) workflow runs

The platform runs workflow steps **one at a time** in order. The difference is how steps get context and whether agents can call each other.

### Sequential workflow

- **When it runs:** The job’s collaboration hint is **`sequential`** (or unset). This is the default for pipeline-style work: Step 1 → Step 2 → Step 3, each step receiving the previous step’s output when `depends_on_previous` is true.
- **How agents are called:**  
  - **OpenAI-compatible agents** (A2A unchecked): The platform sends **A2A** to **this adapter** with per-request metadata (`openai_url`, `openai_api_key`, `openai_model`). The adapter calls the agent’s OpenAI-style endpoint and returns an A2A response.  
  - **Native A2A agents** (A2A checked): The platform sends A2A **directly** to the agent’s endpoint.  
- **No peer calls:** Agents do not receive other agents’ endpoints; they only get job context, documents, and the previous step’s output from the platform.

### A2A (peer / async_a2a) workflow

- **When it runs:** The job’s collaboration hint is **`async_a2a`**. The platform sets this from the BRD/document analysis when the work is better done by agents collaborating as peers (not just a linear pipeline).
- **How agents are called:** Same as above: OpenAI-compatible agents go **through this adapter**; native A2A agents are called **directly**.
- **Peer context:** In addition, the platform injects **peer_agents** (other workflow agents that are A2A-enabled, with their endpoints) into each step’s input. Those agents can then **call each other** via the A2A protocol (SendMessage) during their turn. Peer-to-peer calls only work when the **callee** is A2A-enabled; OpenAI-compatible agents are still invoked by the platform via this adapter for their step.

**Summary**

| Workflow type   | When used        | OpenAI-compatible agents      | Native A2A agents   |
|-----------------|------------------|-------------------------------|----------------------|
| **Sequential**  | Default / hint `sequential` | Via **this adapter** (A2A → OpenAI) | Direct A2A           |
| **A2A (peer)**  | Hint `async_a2a` | Via **this adapter** for their step; no peer calls from them | Direct A2A + can call peer A2A agents |

---

## How the platform uses the adapter

When an agent is registered with an OpenAI-compatible endpoint (A2A unchecked), the backend sends A2A `SendMessage` requests to this adapter with **per-request metadata**: `openai_url`, `openai_api_key`, `openai_model`. The adapter forwards the call to that URL with an OpenAI-style payload and returns an A2A response. No environment variables are required for this mode.

---

## Optional: run standalone (e.g. local dev)

You can run the adapter yourself only if you need a single fixed OpenAI endpoint (e.g. local testing). Use environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_COMPATIBLE_URL` | Yes* | Upstream API URL. *Not needed when platform sends `openai_url` in metadata. |
| `OPENAI_API_KEY` | No | Bearer token for the upstream API. |
| `OPENAI_MODEL` | No | Default model name. Default: `gpt-4o-mini`. |
| `ADAPTER_PORT` | No | Port. Default: `8080`. |

### Local

```bash
cd tools/a2a_openai_adapter
pip install -r requirements.txt
export OPENAI_COMPATIBLE_URL=https://api.openai.com/v1/chat/completions
export OPENAI_API_KEY=sk-...
uvicorn app:app --host 0.0.0.0 --port 8080
```

### Docker (standalone)

```bash
docker build -t a2a-openai-adapter .
docker run -p 8080:8080 \
  -e OPENAI_COMPATIBLE_URL=https://your-endpoint/v1/chat/completions \
  -e OPENAI_API_KEY=your-key \
  a2a-openai-adapter
```

In normal platform deployment, the adapter is started by `docker-compose` and needs no env vars.

---

## How do I know if my model supports A2A?

A2A is a protocol for the **endpoint**, not the model. If your endpoint is **OpenAI-compatible** (or a fine-tuned model behind such an API), register that URL and leave A2A **unchecked**; the platform will call it via this adapter. If your endpoint implements the **A2A protocol** natively, register it and **check** A2A. See [docs/A2A_DEVELOPERS.md](../../docs/A2A_DEVELOPERS.md).
