# Knowledge base

Public European Commission press releases and speeches used by `search_press_releases`.

* `*.md`: one document per file, first block is JSON front matter between `---` lines
  (`title`, `date`, `type`, `reference`, `source_file`), then the text.
* `*.pdf`: Press Corner PDFs can be dropped in as they are; title, date and reference are parsed from the text.

Seed set (supplied for the PoC): IP/25/881, IP/25/1277, IP/25/1307, IP/25/1343, SPEECH/25/1297.
Restart the agent after adding files; the BM25 index is built at start-up.
