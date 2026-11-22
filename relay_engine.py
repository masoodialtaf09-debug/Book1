#!/usr/bin/env python3
"""Relay Engine: runs a project through six AI models in a loop and finishes in Claude.

Usage:  python relay_engine.py project.json [--rounds 3] [--dry-run]
"""
import argparse, contextvars, json, os, pathlib, re, sys, time
import requests

TIMEOUT, MAX_TOKENS, MAX_CTX = 180, 4096, 24000
env = os.environ.get

PROVIDERS = {
    "claude":     dict(key="ANTHROPIC_API_KEY",  model=env("CLAUDE_MODEL", "claude-opus-5-5")),
    "chatgpt":    dict(key="OPENAI_API_KEY",     model=env("OPENAI_MODEL", "gpt-4.1"),
                       url="https://api.openai.com/v1/chat/completions"),
    "perplexity": dict(key="PERPLEXITY_API_KEY", model=env("PERPLEXITY_MODEL", "sonar-pro"),
                       url="https://api.perplexity.ai/chat/completions"),
    "deepseek":   dict(key="DEEPSEEK_API_KEY",   model=env("DEEPSEEK_MODEL", "deepseek-chat"),
                       url="https://api.deepseek.com/chat/completions"),
    "gemini":     dict(key="GEMINI_API_KEY",     model=env("GEMINI_MODEL", "gemini-2.5-pro")),
    # Copilot has no general API; GitHub Models is the closest programmatic stand-in.
    "copilot":    dict(key="GITHUB_TOKEN",       model=env("COPILOT_MODEL", "openai/gpt-4.1"),
                       url="https://models.github.ai/inference/chat/completions"),
}
SYSTEM = "You are a precise, concise collaborator on a multi-model project. Be specific and flag doubts."


_emit = contextvars.ContextVar("emit", default=print)


def log(msg):
    _emit.get()(msg)


def call(name, prompt, system=SYSTEM):
    """Call one provider. Retries, then falls back to Claude if another model is unavailable."""
    p = PROVIDERS[name]
    key = env(p["key"])
    if not key:
        if name == "claude":
            sys.exit("ANTHROPIC_API_KEY is required: Claude runs intake and the final stage.")
        log(f"  ! {p['key']} not set, using Claude instead of {name}")
        return call("claude", prompt, system)
    for attempt in range(3):
        try:
            if name == "claude":
                r = requests.post("https://api.anthropic.com/v1/messages",
                                  headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                                  json={"model": p["model"], "max_tokens": MAX_TOKENS, "system": system,
                                        "messages": [{"role": "user", "content": prompt}]}, timeout=TIMEOUT)
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json()["content"])
            if name == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{p['model']}:generateContent"
                r = requests.post(url, headers={"x-goog-api-key": key}, timeout=TIMEOUT,
                                  json={"systemInstruction": {"parts": [{"text": system}]},
                                        "contents": [{"role": "user", "parts": [{"text": prompt}]}]})
                r.raise_for_status()
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            r = requests.post(p["url"], headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT,
                              json={"model": p["model"], "max_tokens": MAX_TOKENS,
                                    "messages": [{"role": "system", "content": system},
                                                 {"role": "user", "content": prompt}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, IndexError) as e:
            log(f"  ! {name} attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)
    if name == "claude":
        sys.exit("Claude failed three times. Check your key and network.")
    log(f"  ! {name} unavailable, using Claude")
    return call("claude", prompt, system)


def clip(text):
    return text if len(text) <= MAX_CTX else text[:MAX_CTX] + "\n[...truncated...]"


def make_prompt(d, brief, prev, role, task, ret):
    return (f"GOAL: {d['goal']}\n\nPROJECT BRIEF:\n{clip(brief)}\n\nPREVIOUS OUTPUT:\n{clip(prev) or '(none yet)'}\n\n"
            f"YOUR ROLE: {role}\n\nTASK: {task}\n\nRETURN: {ret}\nAlso list what you changed and anything you doubt.")


def base_brief(d):
    return (f"PROJECT BRIEF\nName: {d['name']}\nType: {d.get('type', 'general')}\nGoal: {d['goal']}\n"
            f"Audience: {d.get('audience') or 'not set'}\nConstraints: {d.get('constraints') or 'not set'}\n"
            f"Needs: {', '.join(d.get('needs', [])) or 'none'}")


def read_files(paths):
    out = []
    for f in paths:
        try:
            out.append(f"--- {f} ---\n" + pathlib.Path(f).read_text(errors="ignore")[:100000])
        except OSError as e:
            log(f"  ! could not read {f}: {e}")
    return "\n\n".join(out)


def plan(d):
    n = set(d.get("needs", []))
    builder = "copilot" if "code" in n else "chatgpt"
    stages = ["intake"]
    if "facts" in n: stages.append("research")
    if "docs" in n: stages.append("docs")
    stages += ["options"]
    if n & {"code", "office"}: stages.append("build")
    stages += ["stress-loop", "final"]
    return stages, builder


CONNECTORS = {"facts": "Web search", "code": "GitHub + Supabase", "office": "Google Sheets / Canva",
              "people": "Gmail, Calendar, Zoom", "money": "Stripe", "automate": "Zapier or n8n"}


def run(d, rounds, dry, base="runs"):
    stages, builder = plan(d)
    log(f"Plan for '{d['name']}': " + " > ".join(stages) + f"  (builder: {builder}, max {rounds} rounds)")
    if dry:
        return
    out = pathlib.Path(base) / (re.sub(r"\W+", "-", d["name"].lower()).strip("-") + time.strftime("-%Y%m%d-%H%M%S"))
    out.mkdir(parents=True)
    step = [0]

    def save(label, text):
        step[0] += 1
        (out / f"{step[0]:02d}-{label}.md").write_text(text)

    log_lines, brief, work, research = [], base_brief(d), "", ""

    def note(model, what):
        log_lines.append(f"- {model}: {what}")

    log("Intake (claude)")
    brief = call("claude", make_prompt(d, brief, "", "Project lead.",
                 "Tighten this brief: goal, audience, constraints, success criteria, top three risks. "
                 "Assume sensible answers where unknown and list them as assumptions.",
                 "The full updated project brief only."))
    save("intake-claude", brief); note("claude", "tightened brief")

    if "research" in stages:
        log("Research (perplexity)")
        research = call("perplexity", make_prompt(d, brief, "", "Researcher.",
                        "Find current, verifiable information needed for this project. Cite a source link for every claim.",
                        "Findings by topic with source links, and a list of claims you could not verify."))
        save("research-perplexity", research); note("perplexity", "sourced findings")
    if "docs" in stages:
        log("Document digest (gemini)")
        docs = read_files(d.get("files", []))
        research += "\n\n" + call("gemini", make_prompt(d, brief, research, "Document analyst.",
                   "Read the documents below, extract what matters for the goal, and cross-check the research.\n\n" + clip(docs),
                   "Key findings, contradictions with the research, and unverified claims."))
        save("docs-gemini", research); note("gemini", "document digest and cross-check")

    log("Options (chatgpt)")
    work = call("chatgpt", make_prompt(d, brief, research, "Strategist and first-draft writer.",
                "Propose three approaches with trade-offs, recommend one, and write a first draft of it.",
                "Three options, the recommendation, and the draft."))
    save("options-chatgpt", work); note("chatgpt", "options and first draft")

    if "build" in stages:
        log(f"Build ({builder})")
        work = call(builder, make_prompt(d, brief, work, "Builder.",
                    "Produce the working deliverable (code, spreadsheet structure, deck or document) from the recommended option.",
                    "The deliverable, setup steps and known limits."))
        save(f"build-{builder}", work); note(builder, "built deliverable")

    for r in range(1, rounds + 1):
        log(f"Stress test round {r} (deepseek)")
        crit = call("deepseek", make_prompt(d, brief, work, "Skeptical reviewer.",
                    "Assume this will fail. Find errors in logic, numbers, code and assumptions. Rank by severity.",
                    "A ranked problem list with a fix for each, ending with exactly one line: VERDICT: ship, VERDICT: revise or VERDICT: rethink."))
        save(f"stress-r{r}-deepseek", crit)
        m = re.search(r"VERDICT:\s*(ship|revise|rethink)", crit, re.I)
        verdict = m.group(1).lower() if m else "revise"
        note("deepseek", f"round {r} verdict {verdict}")
        log(f"  verdict: {verdict}")
        if verdict == "ship" or r == rounds:
            break
        who = "chatgpt" if verdict == "rethink" else (builder if "build" in stages else "chatgpt")
        log(f"Revise ({who})")
        work = call(who, make_prompt(d, brief, work + "\n\nREVIEWER FEEDBACK:\n" + crit, "Reviser.",
                    "Fix every high and medium severity problem. Keep what already works.",
                    "The complete revised deliverable."))
        save(f"revise-r{r}-{who}", work); note(who, f"revision after round {r}")

    log("Final version (claude)")
    final = call("claude", make_prompt(d, brief + "\n\nMODEL LOG\n" + "\n".join(log_lines),
                 f"RESEARCH AND SOURCES:\n{research or '(none)'}\n\nLATEST DELIVERABLE:\n{work}",
                 "Editor-in-chief.",
                 "Resolve conflicts between the models, keep the strongest parts, remove duplication, "
                 "mark every unverified claim, and write the finished deliverable.",
                 "The final deliverable, then a short list of next steps."))
    (out / "final.md").write_text(final)
    (out / "brief.md").write_text(brief + "\n\nMODEL LOG\n" + "\n".join(log_lines))
    log(f"\nDone. Files in {out}/ (start with final.md)")
    tools = [v for k, v in CONNECTORS.items() if k in d.get("needs", [])]
    if tools:
        log("Connectors worth adding next: " + "; ".join(tools))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="show the plan without calling any model")
    a = ap.parse_args()
    run(json.loads(pathlib.Path(a.project).read_text()), max(1, min(a.rounds, 5)), a.dry_run)
