---
applyTo: "template/chat-client/**"
---

# Building a Customer (Client-Side) Agent

## What the template already gives you

Before writing any code, understand what ships in the template:

| Already built | Where |
|---|---|
| Chat UI with message thread | `chat-client/src/App.tsx` |
| `POST /a2a` call with `contextId` multi-turn | `App.tsx` — `sendMessage()` function |
| Tool event trace panel | Side panel in `App.tsx`, reads `metadata.tool_events` |
| UCP checkout card (payment flow) | Checkout component in `App.tsx` |
| WebSocket trace (`/ws/trace`) listener | Connected in `App.tsx` |

**If you just want a custom store UI**: update branding, quick prompts, and `REACT_APP_AGENT_URL`. The protocol layer is done.

**If you want a client-side reasoning agent** (e.g. a Node.js or LangGraph process that drives the shopping flow autonomously): the sections below explain the A2A wire format you need to speak.

---

## A2A protocol basics

Every message to your agent is a JSON-RPC 2.0 envelope:

```json
{
  "jsonrpc": "2.0",
  "id": "<uuid>",
  "method": "message/send",
  "params": {
    "contextId": "<session-uuid>",
    "message": {
      "parts": [
        { "kind": "text", "text": "Show me what you have in stock" }
      ]
    }
  }
}
```

Send it to `POST /a2a` on your agent.

### Key fields

| Field | Purpose |
|---|---|
| `id` | Unique per request (uuid) |
| `contextId` | **Same value for the entire session** — this is how the agent maintains conversation history. Generate once, reuse for all turns. |
| `message.parts[].kind` | `"text"` for user messages |

### Reading the response

```json
{
  "jsonrpc": "2.0",
  "id": "<same uuid>",
  "result": {
    "artifacts": [
      {
        "parts": [
          { "kind": "text", "text": "Here are our bestsellers: ..." }
        ]
      }
    ],
    "metadata": {
      "tool_events": [
        { "tool": "get_bestsellers", "args": {}, "result": {...} }
      ]
    }
  }
}
```

The text response is at `result.artifacts[0].parts[0].text`.  
The tools the agent called are at `result.metadata.tool_events[].tool`.

## Multi-turn conversation (contextId)

Generate one contextId at session start and pass it with every request:

```typescript
const contextId = crypto.randomUUID();

async function sendMessage(text: string): Promise<string> {
  const res = await fetch(`${AGENT_URL}/a2a`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: crypto.randomUUID(),   // new per request
      method: "message/send",
      params: {
        contextId,               // same for whole session
        message: { parts: [{ kind: "text", text }] },
      },
    }),
  });
  const body = await res.json();
  if (body.error) throw new Error(body.error.message ?? body.error);
  return body.result.artifacts?.[0]?.parts?.[0]?.text ?? "";
}
```

## Reading tool events (building a trace UI)

`metadata.tool_events` lets you show what the agent did:

```typescript
const toolEvents: ToolEvent[] = body.result?.metadata?.tool_events ?? [];
toolEvents.forEach(event => {
  console.log(`Agent called: ${event.tool}`, event.args, event.result);
});
```

The template's chat client renders these in the side panel as "agent traces". Wire up the WebSocket endpoint (`/ws/trace`) on the agent to get real-time streaming tool events.

## Building a shopping loop (client-side agent pattern)

If you want a client-side agent (e.g. a LangGraph graph running in the browser or a Node.js process), structure the loop like this:

```typescript
async function shoppingLoop(userIntent: string) {
  // 1. Send the user's message
  const response = await sendMessage(userIntent);
  const tools = getToolEvents(response);

  // 2. React to what the agent did
  if (tools.includes("create_checkout_session")) {
    // Agent created a checkout — read the session URL from the artifact
    // and present the UCP/AP2 payment flow
    const checkoutUrl = extractCheckoutUrl(response);
    window.location.href = checkoutUrl;
    return;
  }

  // 3. If agent asked a clarifying question, wait for user input
  // 4. Otherwise, display the response and wait for next turn
}
```

## Checkout flow

When `create_checkout_session` appears in tool events, the agent embeds the checkout session ID in the tool event result. The template's checkout card reads it like this:

```typescript
const checkoutEvent = toolEvents.find(e => e.tool === "create_checkout_session");
const checkoutId = checkoutEvent?.result?.session_id;

// Then drive the UCP lifecycle against the agent:
// PUT  /ucp/checkout/{id}             — update address / shipping option
// POST /ucp/checkout/{id}/complete    — move to READY_FOR_PAYMENT
// POST /ucp/checkout/{id}/confirm     — place order, receive AP2 token
```

**UCP (Google)** and **ACP (OpenAI/Stripe)** have different checkout flows:
- **UCP**: your agent hosts the checkout REST API. Google's UI handles the payment step, then calls your `/complete` endpoint.
- **ACP**: OpenAI/Stripe host the payment page. Your agent returns a session URL; the client redirects to it.

The template implements UCP. To switch to ACP, update the checkout endpoints in `merchant-agent/main.py` and the `UCP_PROFILE` / agent card config.

## Environment variable

The chat client reads `REACT_APP_AGENT_URL` at build time:

```
REACT_APP_AGENT_URL=http://localhost:10999
```

Set it in `.env.local` for local development.
