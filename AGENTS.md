# Repository Guidelines for Agents

This repository contains a Python 3.11 project that scrapes "Item Not Found" metrics from Amazon Seller Central. The code lives in the repository root and is executed with `python inf.py`.

All contributions should keep the project runnable with `python inf.py` and maintain a consistent style throughout the codebase.

## Style conventions
- Target Python 3.11 and use type hints where practical.
- Use 4 spaces for indentation and keep line length under 88 characters.
- Format code with `black` (run `black .` before committing). You may need to `pip install black`.
- Keep imports grouped and sorted (standard library, third party, local).
- Document functions and modules with docstrings.
- Prefer f-strings for string interpolation.
- Break complex logic into smaller functions to improve readability.

## Commit guidelines
- Write commit titles in the imperative mood and keep them under 72 characters.
- Add explanatory details in the body when a change is not obvious.
- Reference related issues or pull requests when relevant.
- Run all programmatic checks before committing to ensure the tree stays clean.

## Programmatic checks
- Install dependencies with `pip install -r requirements.txt`.
- Run `python -m py_compile auth.py inf.py scraper.py notifications.py settings.py` to catch syntax errors.
- Optionally run `black --check .` to ensure formatting.
- If unit tests are added, run them with `pytest`.
- When updating dependencies, ensure the project still runs with `python inf.py`.

## PR expectations
- Summarize the change clearly in the PR body.
- Do not commit `config.json`, `state.json`, or the `output/` directory.
- Describe any new environment variables or configuration values in `README.md`.
- Include the results of programmatic checks in the PR description.
- Keep pull requests focused on a single concern whenever possible.

