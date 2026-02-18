import { FormEvent, useMemo, useState } from "react";
import { MessageSquare, Send } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { executeAiChat } from "@/lib/aiGatewayClient";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

function nextId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function AiChatModal() {
  const suggestions = [
    "What domains can I query?",
    "How many projects do we have?",
    "Show projects with name \"Falcon\"",
    "Sum total project volume",
  ];
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: nextId(),
      role: "assistant",
      content:
        "Ask about project data. Use quotes for name filters, e.g. Show projects with name \"Falcon\".",
    },
  ]);
  const [lastPlan, setLastPlan] = useState("-");

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { id: nextId(), role: "user", content: question }]);
    setLoading(true);
    try {
      const response = await executeAiChat(question);
      const queryPlan = response.query_request;
      const planPreview =
        queryPlan && typeof queryPlan === "object"
          ? `${String(queryPlan.operation || "query")} on ${String(queryPlan.domain || "-")}`
          : "-";
      setLastPlan(planPreview);
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "assistant", content: response.answer || "No answer returned." },
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to query AI gateway.";
      setMessages((prev) => [...prev, { id: nextId(), role: "assistant", content: message }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Button
        className="relative gap-2 bg-white/95 shadow-lg"
        variant="outline"
        onClick={() => setOpen(true)}
        aria-label="Open AI chat"
      >
        <MessageSquare className="h-4 w-4" />
        AI Chat
      </Button>

      <Dialog open={open} onClose={() => setOpen(false)} title="AI Chat" className="max-w-3xl">
        <div className="grid h-[66vh] grid-rows-[1fr_auto_auto] gap-3">
          <div className="overflow-auto rounded-md border border-border bg-white p-3">
            <div className="grid gap-2">
              {messages.map((message) => (
                <article
                  key={message.id}
                  className={
                    message.role === "user"
                      ? "ml-auto max-w-[85%] rounded-md bg-slate-800 px-3 py-2 text-sm text-white"
                      : "mr-auto max-w-[85%] rounded-md border border-border bg-slate-50 px-3 py-2 text-sm"
                  }
                >
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide opacity-70">{message.role}</p>
                  <p className="whitespace-pre-wrap">{message.content}</p>
                </article>
              ))}
            </div>
          </div>

          <div className="rounded-md border border-dashed border-border bg-slate-50 px-3 py-2 text-xs text-slate-600">
            Planner: {lastPlan}
          </div>

          <div className="flex flex-wrap gap-2">
            {suggestions.map((suggestion) => (
              <Button
                key={suggestion}
                type="button"
                variant="outline"
                className="h-8 rounded-full px-3 text-xs"
                onClick={() => setInput(suggestion)}
              >
                {suggestion}
              </Button>
            ))}
          </div>

          <form className="flex items-center gap-2" onSubmit={submit}>
            <input
              className="flex-1 rounded-md border border-border bg-white px-3 py-2 text-sm"
              placeholder="Ask about project or derivative data..."
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
            <Button type="submit" disabled={!canSend} className="gap-2" variant="default">
              <Send className="h-4 w-4" />
              {loading ? "Sending" : "Send"}
            </Button>
          </form>
        </div>
      </Dialog>
    </>
  );
}
