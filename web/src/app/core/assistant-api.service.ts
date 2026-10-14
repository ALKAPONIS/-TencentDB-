import { Injectable } from '@angular/core';

/** One atom of long-term memory (TencentDB Agent Memory L1). */
export interface MemoryAtom {
    id: string;
    type: string | null;
    content: string;
    background?: string;
    score?: number | null;
    updated_at?: string | null;
}

export interface Source {
    title: string;
    date: string;
    reference: string;
    url: string;
    type: string;
}

/** What the agent service recorded for one turn (GET /api/turn/{id}). */
export interface TurnTrace {
    turn_id: string;
    status: 'running' | 'done';
    model?: string;
    duration_s?: number;
    memory?: { available: boolean; persona_used?: boolean; atoms?: MemoryAtom[]; error?: string };
    memory_recalled?: MemoryAtom[];
    memory_deleted?: string[];
    memory_write?: string;
    tools?: { name: string; query?: string; hits?: number; memory_id?: string }[];
    sources?: Source[];
    token_usage?: Record<string, number> | null;
    step_count?: number;
    tool_call_counts?: Record<string, number>;
    exit_reason?: string;
}

export interface MemorySnapshot {
    user_id: string;
    atoms: MemoryAtom[];
    atoms_total: number;
    persona: string;
    scenarios: { path: string; summary?: string; updated_at?: string }[];
    conversation_messages: number;
    error: string;
}

export interface Health {
    status: string;
    assistant_model: string;
    llm_configured: boolean;
    knowledge_documents: number;
    memory: { enabled: boolean; ready: boolean; team_id: string; agent_id: string; fts_available: boolean | null; version: string; error: string };
}

export interface QuotaRow {
    model: string;
    consumed_total_tokens?: number;
    quota?: number;
    error?: string;
}

export interface ChatTurn {
    role: 'user' | 'assistant';
    content: string;
}

const USER_KEY = 'ec-va.user-id';

function uuid(): string {
    return globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Talks to the agent service through the same origin (Nginx forwards /api and /v1).
 * Uses fetch rather than HttpClient so the chat answer can be read as a token stream.
 */
@Injectable({ providedIn: 'root' })
export class AssistantApiService {
    readonly userId: string = this.loadUserId();

    private loadUserId(): string {
        try {
            const existing = localStorage.getItem(USER_KEY);
            if (existing) {
                return existing;
            }
            const id = `user-${uuid()}`;
            localStorage.setItem(USER_KEY, id);
            return id;
        } catch {
            return `user-${uuid()}`; // storage blocked: memory lasts for this page only
        }
    }

    newId(prefix: string): string {
        return `${prefix}-${uuid()}`;
    }

    /** Streams the answer; calls onDelta for each text fragment. Resolves with the full text. */
    async streamChat(
        history: ChatTurn[],
        ids: { sessionId: string; turnId: string },
        onDelta: (text: string) => void,
        signal?: AbortSignal,
    ): Promise<string> {
        const res = await fetch('/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal,
            body: JSON.stringify({
                model: 'ec_assistant',
                stream: true,
                user: this.userId,
                metadata: { session_id: ids.sessionId, turn_id: ids.turnId },
                messages: history,
            }),
        });
        if (!res.ok || !res.body) {
            throw new Error(`The assistant service answered HTTP ${res.status}.`);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let full = '';
        for (;;) {
            const { value, done } = await reader.read();
            if (done) {
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            const events = buffer.split('\n\n');
            buffer = events.pop() ?? '';
            for (const event of events) {
                for (const line of event.split('\n')) {
                    if (!line.startsWith('data: ')) {
                        continue;
                    }
                    const data = line.slice(6).trim();
                    if (data === '[DONE]') {
                        continue;
                    }
                    try {
                        const chunk = JSON.parse(data);
                        const delta: string = chunk?.choices?.[0]?.delta?.content ?? '';
                        if (delta) {
                            full += delta;
                            onDelta(delta);
                        }
                    } catch {
                        // keep-alive or partial line: ignore
                    }
                }
            }
        }
        return full;
    }

    /** Polls the turn trace until the agent marks it done (or gives up). */
    async turnTrace(turnId: string, attempts = 12): Promise<TurnTrace | null> {
        for (let i = 0; i < attempts; i++) {
            const res = await fetch(`/api/turn/${encodeURIComponent(turnId)}`);
            if (res.ok) {
                const trace = (await res.json()) as TurnTrace;
                if (trace.status === 'done' && trace.memory_write !== 'pending') {
                    return trace;
                }
                if (i === attempts - 1) {
                    return trace;
                }
            }
            await new Promise((r) => setTimeout(r, 500));
        }
        return null;
    }

    async health(): Promise<Health | null> {
        return this.getJson<Health>('/api/health');
    }

    async memory(): Promise<MemorySnapshot | null> {
        return this.getJson<MemorySnapshot>(`/api/memory?user_id=${encodeURIComponent(this.userId)}`);
    }

    async deleteMemory(atomId: string): Promise<void> {
        await fetch(`/api/memory/${encodeURIComponent(atomId)}?user_id=${encodeURIComponent(this.userId)}`, { method: 'DELETE' });
    }

    async forgetEverything(): Promise<Record<string, unknown> | null> {
        const res = await fetch('/api/memory/forget-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: this.userId }),
        });
        return res.ok ? res.json() : null;
    }

    async quota(): Promise<{ models: QuotaRow[]; error?: string } | null> {
        return this.getJson('/api/quota');
    }

    private async getJson<T>(url: string): Promise<T | null> {
        try {
            const res = await fetch(url);
            return res.ok ? ((await res.json()) as T) : null;
        } catch {
            return null;
        }
    }
}
