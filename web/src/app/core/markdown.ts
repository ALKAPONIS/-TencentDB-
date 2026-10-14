/**
 * Minimal, safe Markdown to HTML for assistant answers.
 * Input is HTML-escaped first; only a small set of constructs is then re-introduced.
 * Angular sanitises [innerHTML] again on top of this.
 */
function escapeHtml(text: string): string {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function inline(text: string): string {
    return text
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
        .replace(
            /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener" class="eui-u-text-link-external">$1</a>',
        );
}

export function renderMarkdown(source: string): string {
    const lines = escapeHtml(source ?? '').split('\n');
    const out: string[] = [];
    let list: 'ul' | 'ol' | null = null;
    let paragraph: string[] = [];

    const flushParagraph = (): void => {
        if (paragraph.length) {
            out.push(`<p>${inline(paragraph.join('<br>'))}</p>`);
            paragraph = [];
        }
    };
    const closeList = (): void => {
        if (list) {
            out.push(`</${list}>`);
            list = null;
        }
    };

    for (const raw of lines) {
        const line = raw.trimEnd();
        const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
        const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
        const heading = /^#{1,4}\s+(.*)$/.exec(line);
        if (bullet || numbered) {
            flushParagraph();
            const kind = bullet ? 'ul' : 'ol';
            if (list !== kind) {
                closeList();
                out.push(`<${kind}>`);
                list = kind;
            }
            out.push(`<li>${inline((bullet ?? numbered)![1])}</li>`);
        } else if (heading) {
            flushParagraph();
            closeList();
            out.push(`<p class="answer-heading"><strong>${inline(heading[1])}</strong></p>`);
        } else if (!line.trim()) {
            flushParagraph();
            closeList();
        } else {
            closeList();
            paragraph.push(line);
        }
    }
    flushParagraph();
    closeList();
    return out.join('');
}
