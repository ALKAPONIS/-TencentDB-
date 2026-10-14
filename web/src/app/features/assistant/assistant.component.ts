import { Component, ElementRef, OnDestroy, OnInit, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { EUI_PAGE } from '@eui/components/eui-page';
import { EUI_CARD } from '@eui/components/eui-card';
import { EUI_BUTTON } from '@eui/components/eui-button';
import { EUI_TEXTAREA } from '@eui/components/eui-textarea';
import { EUI_CHIP } from '@eui/components/eui-chip';
import { EUI_BADGE } from '@eui/components/eui-badge';
import { EUI_ICON } from '@eui/components/eui-icon';
import { EUI_ICON_BUTTON } from '@eui/components/eui-icon-button';
import { EUI_ALERT } from '@eui/components/eui-alert';
import { EUI_PROGRESS_BAR } from '@eui/components/eui-progress-bar';

import {
    AssistantApiService,
    ChatTurn,
    Health,
    MemorySnapshot,
    QuotaRow,
    TurnTrace,
} from '../../core/assistant-api.service';
import { renderMarkdown } from '../../core/markdown';

interface Message {
    role: 'user' | 'assistant';
    content: string;
    html?: string;
    turnId?: string;
    streaming?: boolean;
    error?: string;
    trace?: TurnTrace | null;
}

const SUGGESTIONS = [
    'From now on, answer me in Greek and keep answers to three sentences.',
    'What emergency support did farmers in Czechia, Slovenia and Germany receive?',
    'Which car manufacturers were fined for the end-of-life vehicles cartel, and why?',
    'I am a journalist covering EU humanitarian aid. What did Commissioner Lahbib announce?',
    'What do you remember about me?',
];

@Component({
    templateUrl: './assistant.component.html',
    styleUrl: './assistant.component.scss',
    imports: [
        FormsModule,
        ...EUI_PAGE,
        ...EUI_CARD,
        ...EUI_BUTTON,
        ...EUI_TEXTAREA,
        ...EUI_CHIP,
        ...EUI_BADGE,
        ...EUI_ICON,
        ...EUI_ICON_BUTTON,
        ...EUI_ALERT,
        ...EUI_PROGRESS_BAR,
    ],
})
export class AssistantComponent implements OnInit, OnDestroy {
    private readonly api = inject(AssistantApiService);
    private readonly thread = viewChild<ElementRef<HTMLElement>>('thread');
    private abort: AbortController | null = null;
    private refreshTimer: ReturnType<typeof setInterval> | null = null;

    readonly suggestions = SUGGESTIONS;
    readonly userId = this.api.userId;
    readonly messages = signal<Message[]>([]);
    readonly draft = signal('');
    readonly busy = signal(false);
    readonly health = signal<Health | null>(null);
    readonly healthChecked = signal(false);
    readonly memory = signal<MemorySnapshot | null>(null);
    readonly memoryLoading = signal(false);
    readonly quota = signal<QuotaRow[] | null>(null);
    readonly notice = signal<string>('');
    sessionId = this.api.newId('session');

    readonly lastTrace = computed<TurnTrace | null>(() => {
        const withTrace = this.messages().filter((m) => m.role === 'assistant' && m.trace);
        return withTrace.length ? withTrace[withTrace.length - 1].trace ?? null : null;
    });
    readonly memoryReady = computed(() => !!this.health()?.memory?.ready);
    readonly usedAtoms = computed(() => {
        const t = this.lastTrace();
        const all = [...(t?.memory?.atoms ?? []), ...(t?.memory_recalled ?? [])];
        return all.filter((a, i) => all.findIndex((b) => b.id === a.id) === i);
    });

    async ngOnInit(): Promise<void> {
        await this.loadHealth();
        await this.refreshMemory();
        // L1-L3 memories are distilled asynchronously by memory-core: refresh the panel periodically.
        this.refreshTimer = setInterval(() => {
            if (document.visibilityState === 'visible' && !this.busy()) {
                void this.refreshMemory();
            }
        }, 30000);
    }

    ngOnDestroy(): void {
        this.abort?.abort();
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
        }
    }

    async loadHealth(): Promise<void> {
        this.health.set(await this.api.health());
        this.healthChecked.set(true);
    }

    async refreshMemory(): Promise<void> {
        if (!this.memoryReady()) {
            return;
        }
        this.memoryLoading.set(true);
        this.memory.set(await this.api.memory());
        this.memoryLoading.set(false);
    }

    useSuggestion(text: string): void {
        this.draft.set(text);
        void this.send();
    }

    onKeydown(event: KeyboardEvent): void {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            void this.send();
        }
    }

    async send(): Promise<void> {
        const text = this.draft().trim();
        if (!text || this.busy()) {
            return;
        }
        this.notice.set('');
        const history: ChatTurn[] = [
            ...this.messages()
                .filter((m) => !m.error && m.content)
                .map((m) => ({ role: m.role, content: m.content })),
            { role: 'user', content: text },
        ];
        const turnId = this.api.newId('turn');
        this.messages.update((list) => [
            ...list,
            { role: 'user', content: text },
            { role: 'assistant', content: '', html: '', turnId, streaming: true },
        ]);
        this.draft.set('');
        this.busy.set(true);
        this.scrollToEnd();

        this.abort = new AbortController();
        try {
            await this.api.streamChat(
                history,
                { sessionId: this.sessionId, turnId },
                (delta) => {
                    this.patchAssistant(turnId, (m) => {
                        const content = m.content + delta;
                        return { ...m, content, html: renderMarkdown(content) };
                    });
                    this.scrollToEnd();
                },
                this.abort.signal,
            );
            this.patchAssistant(turnId, (m) => ({
                ...m,
                streaming: false,
                content: m.content || '(no answer)',
                html: renderMarkdown(m.content || '_(no answer)_'),
            }));
            const trace = await this.api.turnTrace(turnId);
            this.patchAssistant(turnId, (m) => ({ ...m, trace }));
            void this.refreshMemory();
        } catch (err) {
            const aborted = (err as Error)?.name === 'AbortError';
            this.patchAssistant(turnId, (m) => ({
                ...m,
                streaming: false,
                error: aborted ? 'Stopped.' : (err as Error)?.message || 'The assistant is unavailable.',
            }));
        } finally {
            this.busy.set(false);
            this.abort = null;
        }
    }

    stop(): void {
        this.abort?.abort();
    }

    newConversation(): void {
        this.stop();
        this.messages.set([]);
        this.sessionId = this.api.newId('session');
        this.notice.set('New conversation started. The assistant still remembers you from earlier ones.');
    }

    async deleteAtom(id: string): Promise<void> {
        await this.api.deleteMemory(id);
        await this.refreshMemory();
    }

    async forgetEverything(): Promise<void> {
        if (!window.confirm('Erase everything the assistant remembers about you? This cannot be undone.')) {
            return;
        }
        this.memoryLoading.set(true);
        const report = await this.api.forgetEverything();
        this.notice.set(report ? 'Your long-term memory was erased.' : 'Erasing failed. Please try again.');
        await this.refreshMemory();
    }

    async loadQuota(): Promise<void> {
        const res = await this.api.quota();
        this.quota.set(res?.models ?? []);
    }

    tokenTotal(trace: TurnTrace | null): number | null {
        const usage = trace?.token_usage;
        if (!usage) {
            return null;
        }
        return usage['total_tokens'] ?? (usage['prompt_tokens'] ?? 0) + (usage['completion_tokens'] ?? 0);
    }

    toolSummary(trace: TurnTrace | null): string {
        const counts = trace?.tool_call_counts ?? {};
        const parts = Object.entries(counts)
            .filter(([, n]) => n > 0)
            .map(([name, n]) => `${name} ×${n}`);
        return parts.length ? parts.join(', ') : 'none';
    }

    private patchAssistant(turnId: string, fn: (m: Message) => Message): void {
        this.messages.update((list) => list.map((m) => (m.turnId === turnId ? fn(m) : m)));
    }

    private scrollToEnd(): void {
        queueMicrotask(() => {
            const el = this.thread()?.nativeElement;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        });
    }
}
