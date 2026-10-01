// POST /v1/chat as Server-Sent Events. EventSource can't POST, so parse the fetch body stream ourselves.
import { ApiError, toApiError } from "./api.js";

/** Split an SSE buffer into complete events; returns [events, rest]. */
export function parseEvents(buffer) {
  const events = [];
  const blocks = buffer.replace(/\r\n?/g, "\n").split("\n\n");
  const rest = blocks.pop();
  for (const block of blocks) {
    let name = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") name = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) events.push({ name, data: JSON.parse(data.join("\n")) });
  }
  return [events, rest];
}

/**
 * Stream one chat turn. `handlers.on<Event>` gets each event's data: onMeta, onToken, onCitations, onDone, onError.
 * Resolves when the stream ends; rejects with ApiError for HTTP errors before the stream starts or a dropped connection.
 */
export async function streamChat(body, handlers, signal) {
  let response;
  try {
    response = await fetch("/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ ...body, stream: true }),
      signal,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("NETWORK", "Can't reach the server. Check your connection.");
  }
  if (!response.ok) throw await toApiError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let events;
      [events, buffer] = parseEvents(buffer);
      for (const { name, data } of events) {
        const handler = handlers["on" + name.charAt(0).toUpperCase() + name.slice(1)];
        if (handler) handler(data);
        if (name === "done" || name === "error") finished = true;
      }
    }
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("NETWORK", "The connection dropped while answering.");
  }
  if (!finished) throw new ApiError("NETWORK", "The connection dropped while answering.");
}
