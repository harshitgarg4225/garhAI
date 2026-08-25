import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import Anthropic from "@anthropic-ai/sdk";

const MODEL = process.env.GARHAI_MODEL ?? "claude-opus-5";
const MAX_HISTORY_MESSAGES = 40;
const MAX_MESSAGE_CHARS = 32_000;

const SYSTEM_PROMPT = `You are GarhAI, a sharp, friendly personal AI assistant.

Guidelines:
- Be direct and helpful. Lead with the answer, then add context if useful.
- Use Markdown formatting (headings, lists, fenced code blocks) when it improves readability.
- For code, always use fenced code blocks with a language tag.
- If a question is ambiguous, make a reasonable assumption, state it briefly, and answer.
- Keep casual conversation light and natural.`;

const here = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.join(here, "..", "public");

const apiKeyConfigured = Boolean(
  process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN,
);

const client = new Anthropic();

const app = express();
app.disable("x-powered-by");
app.use(express.json({ limit: "1mb" }));
app.use(express.static(publicDir));

app.get("/health", (_req, res) => {
  res.json({
    status: "ok",
    service: "garhai",
    model: MODEL,
    apiKeyConfigured,
  });
});

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

function parseHistory(body: unknown): ChatMessage[] | null {
  if (typeof body !== "object" || body === null) return null;
  const raw = (body as { messages?: unknown }).messages;
  if (!Array.isArray(raw) || raw.length === 0) return null;

  const messages: ChatMessage[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null) return null;
    const { role, content } = entry as { role?: unknown; content?: unknown };
    if (role !== "user" && role !== "assistant") return null;
    if (typeof content !== "string" || content.trim() === "") return null;
    messages.push({ role, content: content.slice(0, MAX_MESSAGE_CHARS) });
  }

  // Keep the tail of long conversations to bound cost and context size.
  let trimmed = messages.slice(-MAX_HISTORY_MESSAGES);
  // The API requires the first message to be from the user.
  while (trimmed.length > 0 && trimmed[0].role !== "user") {
    trimmed = trimmed.slice(1);
  }
  if (trimmed.length === 0 || trimmed[trimmed.length - 1].role !== "user") {
    return null;
  }
  return trimmed;
}

app.post("/api/chat", async (req, res) => {
  const history = parseHistory(req.body);
  if (!history) {
    res.status(400).json({
      error:
        "Request body must be {messages: [{role: 'user'|'assistant', content: string}, ...]} ending with a user message.",
    });
    return;
  }

  if (!apiKeyConfigured) {
    res.status(503).json({
      error:
        "GarhAI is not configured yet: set the ANTHROPIC_API_KEY environment variable (on Railway: service → Variables) and redeploy.",
    });
    return;
  }

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });

  const send = (payload: Record<string, unknown>) => {
    res.write(`data: ${JSON.stringify(payload)}\n\n`);
  };

  const stream = client.beta.messages.stream({
    model: MODEL,
    max_tokens: 64_000,
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    thinking: { type: "adaptive", display: "summarized" },
    system: [
      {
        type: "text",
        text: SYSTEM_PROMPT,
        cache_control: { type: "ephemeral" },
      },
    ],
    messages: history,
  });

  req.on("close", () => {
    stream.abort();
  });

  try {
    for await (const event of stream) {
      if (event.type === "content_block_delta") {
        if (event.delta.type === "text_delta") {
          send({ type: "text", text: event.delta.text });
        } else if (event.delta.type === "thinking_delta") {
          send({ type: "thinking", text: event.delta.thinking });
        }
      }
    }

    const final = await stream.finalMessage();
    if (final.stop_reason === "refusal") {
      send({
        type: "notice",
        text: "GarhAI declined to answer that request for safety reasons. Try rephrasing or asking something else.",
      });
    }
    send({
      type: "done",
      model: final.model,
      stopReason: final.stop_reason,
      usage: {
        inputTokens: final.usage.input_tokens,
        outputTokens: final.usage.output_tokens,
      },
    });
  } catch (error) {
    if (stream.aborted) {
      // Client went away; nothing left to write.
      res.end();
      return;
    }
    send({ type: "error", message: describeApiError(error) });
  }
  res.end();
});

function describeApiError(error: unknown): string {
  if (error instanceof Anthropic.AuthenticationError) {
    return "The server's Anthropic API key was rejected. Check the ANTHROPIC_API_KEY variable.";
  }
  if (error instanceof Anthropic.RateLimitError) {
    return "Rate limit reached. Wait a moment and try again.";
  }
  if (error instanceof Anthropic.APIConnectionError) {
    return "Could not reach the Anthropic API. Check the server's network and try again.";
  }
  if (error instanceof Anthropic.APIError) {
    return `Anthropic API error (${error.status}): ${error.message}`;
  }
  console.error("Unexpected error in /api/chat:", error);
  return "Unexpected server error. Please try again.";
}

// Express 5 named wildcard: send the app shell for any other GET path.
app.get("/*splat", (_req, res) => {
  res.sendFile(path.join(publicDir, "index.html"));
});

const port = Number(process.env.PORT) || 3000;
app.listen(port, "0.0.0.0", () => {
  console.log(`GarhAI listening on port ${port}`);
  console.log(`Model: ${MODEL}`);
  console.log(`API key configured: ${apiKeyConfigured}`);
});
