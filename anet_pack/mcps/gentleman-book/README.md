# Gentleman Book MCP server

Exposes the 18 chapters of the Gentleman Programming Book (software
architecture, clean code) as searchable MCP tools/resources.

Repo-backed, not a package — this folder vendors a **built Go binary**, not
source-as-installed-by-pip/npm. Two upstream repos are involved:

- https://github.com/Alan-TheGentleman/gentleman-book-mcp — the server (Go)
- https://github.com/Alan-TheGentleman/gentleman-programming-book — the book
  content itself (MDX chapters). The server reads this from disk at runtime
  via `BOOK_PATH`; it does not embed the content.

## If this vendored build is missing (fresh pack install)

```bash
cd anet_pack/mcps/gentleman-book

# 1. Server source, build the binary
git clone --depth 1 https://github.com/Alan-TheGentleman/gentleman-book-mcp.git repo
cd repo && go build -o ../bin/gentleman-book-mcp.exe ./cmd/server && cd ..

# 2. Book content — sparse-clone just the chapter data (full repo is ~49MB;
#    this pulls only ~3MB)
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/Alan-TheGentleman/gentleman-programming-book.git book-src
cd book-src && git sparse-checkout set src/data/book && cd ..
```

Requires Go 1.21+ to build. `config.yaml` in this folder already points at
`bin/gentleman-book-mcp.exe` with `BOOK_PATH` set to
`book-src/src/data/book` — no further edits needed once built.

## Tools

- `list_chapters` — all 18 chapters with metadata
- `read_chapter` — read a chapter or section
- `search_book` — keyword search across all content
- `get_book_index` — full table of contents
- `semantic_search` / `build_semantic_index` / `semantic_status` — vector
  search; **needs `OPENAI_API_KEY` or a local Ollama server**, neither is
  configured here, so these report unavailable rather than erroring. Add an
  `env:` key to `config.yaml` if you want semantic search.

## Note on BOOK_PATH

Point `BOOK_PATH` at the **parent** of the `en`/`es` folders
(`src/data/book`), not a language folder directly — the server appends the
language itself and errors ("cannot find the file specified") if pointed at
`src/data/book/en`.
