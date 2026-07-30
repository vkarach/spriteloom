"""How well does Klein's resident Qwen3-4B text encoder do at text2text
tasks beyond title summarization - prompt expansion, critique, rephrasing?
Real GPU needed, no pytest.

Usage:
  .venv\\Scripts\\python -m server.tests.smoke_text2text
"""
import argparse
import sys
import time
import warnings

warnings.filterwarnings("ignore", message="MatMul8bitLt")  # same as server/main.py

TASKS = [
    ("expand a short prompt into a detailed one",
     "a sword",
     "Expand the following short sprite idea into a detailed prompt for a "
     "2D pixel art game sprite generator, 2-3 sentences, describing shape, "
     "colors, and material. Output ONLY the expanded prompt.\n\n"
     "Short idea: {}\n\nExpanded prompt:"),

    ("expand a short prompt into a detailed one",
     "a wizard hat",
     "Expand the following short sprite idea into a detailed prompt for a "
     "2D pixel art game sprite generator, 2-3 sentences, describing shape, "
     "colors, and material. Output ONLY the expanded prompt.\n\n"
     "Short idea: {}\n\nExpanded prompt:"),

    ("suggest one improvement",
     "a knight",
     "Suggest ONE concrete addition to this pixel art sprite prompt that "
     "would make the generated image more distinctive and detailed. Output "
     "ONLY the suggested addition, one short sentence.\n\n"
     "Prompt: {}\n\nSuggested addition:"),

    ("rephrase/vary a prompt",
     "a red apple on a wooden table",
     "Rewrite the following prompt using different words but keeping the "
     "same meaning, as one sentence. Output ONLY the rewritten prompt.\n\n"
     "Prompt: {}\n\nRewritten:"),

    ("sanity: general instruction-following",
     "",
     "List exactly 3 colors that are commonly found in autumn leaves. "
     "Output ONLY a comma-separated list.\n\nColors:"),
]


def _ask(tok, model, torch, text, max_new_tokens):
    has_chat = hasattr(tok, "apply_chat_template") and tok.chat_template
    if has_chat:
        text = tok.apply_chat_template(
            [{"role": "user", "content": text}],
            tokenize=False, add_generation_prompt=True,
            enable_thinking=False)
    inputs = tok(text, return_tensors="pt").to(model.device)
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(
            **inputs, max_new_tokens=max_new_tokens, do_sample=False,
            pad_token_id=tok.eos_token_id)
    dt = time.time() - t0
    new_tokens = out[0][inputs["input_ids"].shape[1]:]
    reply = tok.decode(new_tokens, skip_special_tokens=True).strip()
    return reply, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", action="store_true",
                    help="also run the fixed test battery before the REPL")
    args = ap.parse_args()

    from server.instruct import KleinPipeline
    t0 = time.time()
    pipe = KleinPipeline()
    pipe.load()
    print(f"klein loaded in {time.time() - t0:.1f}s", flush=True)

    import torch
    tok = pipe._pipe.tokenizer
    model = pipe._pipe.text_encoder

    if args.tasks:
        for label, subject, template in TASKS:
            reply, dt = _ask(tok, model, torch, template.format(subject), 96)
            print(f"\n[{label}] [{dt:.2f}s]")
            if subject:
                print(f"  in:  {subject!r}")
            print(f"  out: {reply!r}")

    print("\n--- interactive: type anything, model stays loaded "
         "(empty line or Ctrl-D to quit) ---")
    while True:
        try:
            text = input("\n> ").strip()
        except EOFError:
            break
        if not text:
            break
        reply, dt = _ask(tok, model, torch, text, 200)
        print(f"[{dt:.2f}s] {reply}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
