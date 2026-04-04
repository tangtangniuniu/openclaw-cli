# Repository Guidelines

## Project Structure & Module Organization
This checkout is currently documentation-focused. The only tracked content visible here is under `doc/`, with repository notes in files such as `doc/password_config_and_connect_to_gateway.md`. Add new documentation beside related topics in `doc/`, using short, descriptive snake_case file names.

If code is added later, keep the same layout discipline:
- source modules in a top-level runtime directory such as `src/` or `openclaw_cli/`
- tests in `tests/`
- static assets in `assets/` or `doc/`

## Build, Test, and Development Commands
No build system or test runner is configured in this checkout yet. For now, contributors should focus on document quality checks:

- `ls doc/` to inspect the current document set
- `sed -n '1,120p' doc/<file>.md` to review a file in the terminal
- `rg "<term>" doc/` to find existing guidance before adding new content

When adding executable code, document the exact local workflow here and in the repository README.

## Coding Style & Naming Conventions
Use Markdown with clear heading hierarchy, short paragraphs, and fenced code blocks for commands or examples. Prefer ASCII unless the document must contain localized content. Keep filenames lowercase with underscores, for example `gateway_auth.md`.

For any future code contributions, use the formatter and linter native to that language, keep functions focused, and match the naming style already present in the surrounding module.

## Testing Guidelines
There is no automated test suite in the current workspace. For documentation changes:

- verify headings render correctly
- validate command examples against the actual CLI or environment before publishing
- check cross-references, paths, and environment variable names carefully

If tests are introduced later, place them in `tests/` and mirror the source file or feature name in the test filename.

## Commit & Pull Request Guidelines
Git history is not available in this checkout, so no local commit convention can be inferred from prior commits. Use short, imperative commit messages such as `docs: add gateway password setup guide`.

Pull requests should include:
- a brief summary of what changed
- linked issue or task context when available
- sample command output or rendered screenshots for docs that change user-facing behavior
- notes on any manual validation performed

## Security & Configuration Tips
Do not commit real passwords, tokens, or private gateway endpoints. Use placeholders like `OPENCLAW_GATEWAY_PASSWORD=<value>` in examples, and keep environment-specific secrets in local shell configuration rather than repository files.

## 其他规则
在回复，注释，文档中使用简体中文

