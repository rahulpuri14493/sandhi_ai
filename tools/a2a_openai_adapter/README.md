# A2A ↔ OpenAI-Compatible Adapter

The **platform runs this adapter** as part of the stack. All agent calls that target OpenAI-compatible endpoints go through it so the architecture is A2A everywhere. **Developers do not run it**—they just register their OpenAI-compatible API URL and leave “My endpoint is A2A protocol compliant” unchecked.

## How the platform uses it

When an agent is registered with an OpenAI-compatible endpoint (A2A unchecked), the backend sends A2A `SendMessage` requests to this adapter with **per-request metadata**: `openai_url`, `openai_api_key`, `openai_model`. The adapter forwards the call to that URL with an OpenAI-style payload and returns an A2A response. No environment variables are required for this mode.

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

## How do I know if my model supports A2A?

A2A is a protocol for the **endpoint**, not the model. If your endpoint is **OpenAI-compatible** (or a fine-tuned model behind such an API), register that URL and leave A2A **unchecked**. The platform will call it via this adapter. If your endpoint implements the **A2A protocol** natively, register it and **check** A2A. See [docs/A2A_DEVELOPERS.md](../docs/A2A_DEVELOPERS.md).
