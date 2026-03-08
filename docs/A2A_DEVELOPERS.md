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
