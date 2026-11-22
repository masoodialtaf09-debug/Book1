# Relay Engine

Runs any project through six AI models in a loop and finishes in Claude.

    Intake (Claude) > Research (Perplexity, Gemini) > Options (ChatGPT) > Build (Copilot/ChatGPT)
      > Stress test (DeepSeek) <> Revise, up to 3 rounds > Final version (Claude)

## Setup
    pip install -r requirements.txt
    cp .env.example .env     # add keys, then: set -a; source .env; set +a

Only `ANTHROPIC_API_KEY` is required. Any model without a key falls back to Claude, so you can start with one key and add the rest.

## Run
    python relay_engine.py project.example.json --dry-run   # see the plan, no cost
    python relay_engine.py project.example.json             # real run
    python relay_engine.py my.json --rounds 2

Outputs go to `runs/<project>-<timestamp>/`: one file per stage, `brief.md` with the model log, and `final.md`.

## project.json fields
`name`, `goal` (required); `type`, `audience`, `constraints`; `needs` from: facts, docs, code, office, math, people, money, automate; `files` (text files for Gemini to read when `docs` is set).

## How the loop works
DeepSeek ends each review with `VERDICT: ship | revise | rethink`. Ship stops the loop, revise sends its fix list to the builder, rethink sends it back to ChatGPT. It stops after `--rounds` (default 3, max 5).

## Notes
- Copilot has no general API, so that stage uses GitHub Models (`GITHUB_TOKEN`).
- Model names change often. Confirm the defaults in `.env.example` against each provider's docs.
- Each real run makes roughly 6 to 10 API calls. Start with `--rounds 1`.
- Keep keys in environment variables, never in project files or prompts.
- Outputs from Perplexity carry sources; everything else is unverified until you check it. Claude marks these in `final.md`.
