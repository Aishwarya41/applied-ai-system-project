# VibeFinder 2.0: Build Notes

These notes record what changed in phases 1–3, why each decision was made, and how the pieces fit together. They start from zero: no prior knowledge of RAG, LLM APIs, or "agentic" systems is assumed.

---

## 1. The big picture

### Before (VibeFinder 1.0)

- 28 songs in `data/songs.csv`. Each song has a genre, a mood, and numbers between 0 and 1: energy, valence (how happy it sounds), and acousticness.
- A hand-written scoring function, `score_song()` in `src/recommender.py`, compares one song to a **profile**. A profile is a Python dict such as `{"favorite_genre": "lofi", "target_energy": 0.4}`.
- The profiles were hard-coded in `src/main.py`, so a user couldn't type a request.
- No AI model, no logging, no input checking, and only 2 tests.
- Known problems, documented in the README:
  - `energy: 1.5` silently produced junk.
  - An empty profile returned songs in CSV order.
  - "chill" never matched "relaxed" because labels had to match exactly.

### After (VibeFinder 2.0, so far)

You can type something like *"something mellow for studying on a rainy night, no vocals-heavy pop"*, in the terminal or in a Streamlit web app, and get back:

- 5 songs **from the catalog only**
- a plain-English explanation of each pick
- warnings if the request was odd (out-of-range numbers, contradictions)
- a **trace** showing every step the system took

With a Gemini API key, an AI model understands your words and writes the explanations. Without a key, a rule-based fallback does both, so the app always works.

---

## 2. What is RAG? (From zero)

### The problem RAG solves

A large language model (LLM) such as Gemini has read a huge amount of text and is very good at writing fluent answers. But it **doesn't know your data**. Ask it "recommend 5 songs from my catalog" and it will recommend real or invented songs that aren't in your CSV, and sound completely confident doing it. This is called **hallucination**.

### The idea: an open-book exam

**RAG = Retrieval-Augmented Generation.** Instead of asking the LLM to answer from memory (a closed-book exam), you:

1. **Retrieve:** use ordinary code to find the relevant facts from *your* data.
2. **Augment:** paste those facts into the prompt you send to the LLM.
3. **Generate:** tell the LLM to write its answer *using only those facts*.

The LLM handles the language (understanding the request, writing nicely). Your data supplies the facts. The answer is **grounded**: every claim can be traced back to something you retrieved.

### Key vocabulary

| Term | Meaning in plain words |
|---|---|
| **LLM** | Large language model: an AI that reads and writes text (Gemini, Claude, GPT). |
| **Prompt** | The text you send to the LLM. |
| **Retriever** | The code that finds relevant items in your data. |
| **Candidates** | The items the retriever found, which get handed to the LLM. |
| **Grounded** | The answer only uses facts from the candidates. |
| **Hallucination** | The LLM invents something that isn't in the data. |
| **Structured output** | Forcing the LLM to reply in a fixed JSON format instead of free prose. |
| **Temperature** | How random the LLM's answers are. 0 = as repeatable as possible. |
| **Token** | A chunk of text (about ¾ of a word). APIs measure usage and cost in tokens. |
| **Agentic** | The system plans, acts, *checks its own work*, and corrects itself, instead of making one blind call. |
| **Fallback** | A safe backup path used when the main path fails. |

### Why many RAG tutorials look different

Most tutorials retrieve chunks of documents with **embeddings** (vector search). We didn't need that: our data is a small, structured table, and we already had a well-tuned scorer that ranks songs against a profile. **The existing `score_song()` became our retriever.** It's still RAG: retrieve → augment the prompt → generate from what was retrieved.

---

## 3. How one request flows through the system

Here's a real run of *"something mellow for studying on a rainy night, no vocals-heavy pop"* with Gemini on. Each step names the file that does it.

```
 user text
    │
    ▼
┌─────────┐  1. GUARD      src/guardrails.py  check_query()
│  guard  │     Is it empty? Too long? Unsafe? A prompt-injection attempt?
└────┬────┘     → if so, stop here with a polite message.
     ▼
┌─────────┐  2. PARSE      src/llm.py  parse_with_llm()   (offline: src/offline_parser.py)
│  parse  │     Turn the words into a profile:
└────┬────┘     {genre: lofi, mood: chill, energy: 0.2, valence: 0.3}, avoid: [pop]
     ▼
┌──────────┐ 3. SANITIZE   src/guardrails.py  sanitize_profile()
│ sanitize │    Clamp numbers into 0–1, map words onto real labels, flag contradictions.
└────┬─────┘
     ▼
┌──────────┐ 4. RETRIEVE   src/recommender.py  retrieve_candidates()      ← the "R" in RAG
│ retrieve │    Score all 28 songs, drop excluded genres, keep the top 10 as candidates.
└────┬─────┘
     ▼
┌──────────┐ 5. GENERATE   src/llm.py  generate_with_llm()             ← the "A" + "G" in RAG
│ generate │    Send Gemini the request + the 10 candidates. It picks 5 BY ID and explains them.
└────┬─────┘
     ▼
┌──────────┐ 6. CHECK      src/validator.py  validate_recommendations()   ← the "agentic" part
│  check   │    Are all picked IDs among the candidates? Titles correct? Right count? No duplicates?
└────┬─────┘     ├─ pass → show the answer
     │           ├─ fail → RETRY once, telling Gemini exactly what was wrong
     │           └─ fail again → FALL BACK to the scorer's own top picks
     ▼
  Result (picks + explanations + warnings + trace)  → CLI (src/main.py) or UI (app.py)
```

The whole pipeline is orchestrated by `run_query()` in **`src/agent.py`**.

### Step 1: Guard (before anything else)

Cheap checks run **before** we spend time or money on an API call:

- **Empty text** → ask a clarifying question ("Tell me a bit about the vibe you want…").
- **Over 300 characters** → reject. A song request doesn't need an essay, and long inputs are a common way to sneak in instructions.
- **Prompt injection** ("ignore previous instructions…", "system prompt") → refuse. Prompt injection means a user tries to hijack the LLM by hiding commands in their input.
- **Self-harm language** → a supportive message pointing to the 988 crisis line instead of music.
- **Unsafe requests** (weapons, violence) → refuse.

### Step 2: Parse (understand the request)

The scorer can't read English; it needs a profile dict. So the first job is translation.

- **With Gemini:** we send the text plus the list of *allowed* genres and moods (the ones that actually exist in the CSV). We use **structured output**: a JSON schema whose genre and mood fields are **enums**, meaning the model can only choose from that list. So Gemini *cannot* answer `"k-pop"` or `"sad"`; it has to pick `"pop"` or `"melancholic"`. That fixes the old exact-match bias at the source. Gemini also returns `on_topic: false` for things like "what is the capital of France".
- **Without Gemini (offline):** a rule-based parser looks for keywords:
  - genre and mood words, plus synonyms ("mellow" → chill, "rap" → hip hop)
  - activity words ("study" → low energy and acoustic; "gym" → high energy)
  - negations ("no pop" → exclude pop)
  - explicit numbers ("energy 0.8")

### Step 3: Sanitize (don't trust the parse blindly)

Whatever the parser produced, human-written or AI-written, is checked again:

- Numbers outside 0–1 are **clamped** with a warning (1.5 → 1.0).
- Labels are mapped to real catalog labels: exact match, then synonym table, then typo-tolerant fuzzy match ("jaz" → jazz). Anything still unknown is dropped with a warning.
- **Contradictions** are flagged, e.g. a "melancholic" mood with energy 1.0, or very high energy together with very acoustic.
- If nothing usable is left, we ask a clarifying question instead of returning CSV order (the old empty-profile bug).

### Step 4: Retrieve (the "R")

`retrieve_candidates()` runs the original `score_song()` on every song, removes excluded genres, sorts by score, and returns the **top 10** along with the scorer's reasons ("energy 0.35 is close to target 0.40…").

### Step 5: Generate (the "A" and "G")

We **augment** the prompt: we paste the 10 candidates into it, one JSON line each (id, title, artist, genre, mood, numbers, score, reasons). Gemini is instructed to:

- choose exactly *k* songs **from that list only, cited by ID**
- explain each one in 1–2 sentences using only the data given (no invented lyrics, instruments, or release years)
- write a one-sentence summary

Gemini may **reorder** the candidates. For example, it may put a classical "rain" track first for a rainy study request even though the scorer ranked it lower. That's the value the LLM adds over the plain scorer.

### Step 6: Check, retry, fall back (the "agentic" loop)

An LLM can still ignore instructions. So code, not the LLM, checks the answer:

- every picked ID exists in the retrieved candidates (no hallucinated songs)
- every title matches its ID
- no duplicates
- exactly the expected number of picks
- every pick has an explanation

If the check fails, we call Gemini **again** and include the exact error messages ("#1: song id 999 was not in the retrieved candidates"). Models usually fix a mistake when told precisely what it was. If the second answer also fails, we **fall back** to the scorer's own top picks with template explanations. This **plan → act → check → correct** cycle is what makes it "agentic".

A real model rarely fails this check, so there's a **debug switch** that sabotages Gemini's answer on purpose. That lets you watch the loop work, in the UI sidebar or with `--corrupt 1` / `--corrupt 2` on the command line.

---

## 4. Changes, phase by phase

### Phase 1: Foundation (no AI yet)

**Step 1: Setup**

| File | Change | Why |
|---|---|---|
| `requirements.txt` | Pinned exact versions; added `google-genai`, `python-dotenv` | Pinned versions mean a grader installs exactly what we tested (reproducibility). |
| `.env.example` | Template listing `GEMINI_API_KEY`, `GEMINI_MODEL`, `VIBEFINDER_LOG_LEVEL` | Shows others which settings exist without sharing your secret key. |
| `.gitignore` | Added `.env` and `logs/` | Keeps your API key and runtime logs out of git. |

**Step 2: Guardrails and logging**

| File | What it does |
|---|---|
| `src/guardrails.py` | `check_query()` (pre-checks on raw text), `sanitize_profile()` (clamp, normalize, drop), `detect_conflicts()` (contradiction warnings). |
| `src/vocab.py` | Synonym tables (`GENRE_ALIASES`, `MOOD_ALIASES`) and `normalize_label()`: exact → alias → fuzzy → none. |
| `src/logging_config.py` | Writes every request to `logs/vibefinder.log`: query, parsed profile, retrieved IDs, picks, LLM latency, failures, fallbacks. Each request gets a short ID so you can follow it through the log. |

**Step 3: Offline pipeline**

| File | What it does |
|---|---|
| `src/recommender.py` | Added `retrieve_candidates()`, which returns songs **with** their scores and reasons and supports excluding genres. `recommend_songs()` now calls it but returns exactly what it used to, so the old tests and demo still work. |
| `src/offline_parser.py` | Deterministic keyword parser (same input → same output, always). |
| `src/validator.py` | The check step: `validate_recommendations()` returns a list of problems (empty list = valid). |
| `src/agent.py` | `run_query()`, the orchestrator. Returns a `Result` holding status, mode, profile, warnings, picks, retrieved IDs, and a **trace** (one entry per step, with timing). |
| `src/main.py` | Added `--query/-q`, `--offline`, `-k`. With no `--query` it still runs the original 9-profile demo. |

### Phase 2: Streamlit UI

| File | What it does |
|---|---|
| `app.py` | Chat-style web app. Song cards (title, artist, genre/mood, match %, explanation, numbers); warnings in yellow, blocks in red, clarifying questions in blue; an "Understood as" line showing the parsed profile; a pipeline-trace table. The sidebar has a Gemini switch, a number-of-songs slider, example prompts, clear chat, and the debug sabotage control. |
| `tests/test_app.py` | Drives the UI headlessly (no browser) with Streamlit's `AppTest`. |

### Phase 3: Gemini, RAG, and the self-check loop

| File | What it does |
|---|---|
| `src/llm.py` | The two Gemini calls (`parse_with_llm`, `generate_with_llm`), both temperature 0 with a JSON schema. Every failure (no key, network, timeout, quota, bad JSON) becomes one `LLMError`, so the agent has a single thing to catch. |
| `src/agent.py` | Gemini parse with offline fallback; `_generate_with_checks()` runs generate → check → retry once → give up; fallback to template picks; LLM call and token counting. |
| `app.py`, `src/main.py` | Show Gemini's summary, call and token counts, and the debug sabotage (`--corrupt`). |
| `tests/test_llm_agent.py` | Tests the Gemini paths with a **fake** Gemini: happy path, hallucination → retry, two failures → fallback, timeout, off-topic, clamping. |
| `tests/conftest.py` | Blanks `GEMINI_API_KEY` for every test, so tests never call the real API (free, fast, no key needed). |

---

## 5. Decisions and the reasons behind them

### Decisions you made

| Decision | Why |
|---|---|
| **RAG + agentic check loop** as the core features | Both are required by the rubric; the existing scorer made a natural retriever. |
| **Google Gemini** (not Claude or OpenAI) | Free tier; you already had a key. |
| **Streamlit UI early** (phase 2, not at the end) | So every later feature could be tested by clicking, not just in the terminal. |
| **Reused your existing Gemini key** | Keys belong to your Google account, not a project. That `.env` was never committed to git. |

### Architecture decisions

| Decision | Alternatives considered | Why this one |
|---|---|---|
| Build the **offline path first**, then add Gemini | Start with Gemini | The app and tests work for anyone (graders without a key, no internet, rate limits). Every Gemini failure has somewhere safe to land. |
| **Keep `score_song()` as the retriever** | Embedding/vector search | 28 structured songs don't need embeddings. The scorer is already tuned, explainable, and requires no new dependency. |
| **Retrieve 10, show 5** | Retrieve exactly 5 | Gives Gemini room to reorder and choose, while keeping the prompt short and grounded. |
| **One `run_query()` returning a `Result` with a trace** | Print as we go | The CLI, UI, and tests all reuse the same function. The trace makes the "how" visible, which helps the demo and debugging. |
| **Guard before parsing, sanitize after** | Only validate once | Guarding first saves API calls on junk and blocks injection before the LLM sees it. Sanitizing after means we don't blindly trust *either* parser's numbers. |
| Parsers **copy explicit numbers as-is** (e.g. 1.5), and only guardrails clamp | Let the parser clamp | One place owns clamping, and the warning is visible to the user, which demonstrates the guardrail. |

### LLM decisions

| Decision | Why |
|---|---|
| **Structured JSON output with enums** for genre/mood | The model can't invent labels; fixes the "chill ≠ relaxed" exact-match bias. |
| **Temperature 0** | Most repeatable answers (reproducibility). |
| **Request text wrapped in `<request>` tags and labeled as data** | Basic prompt-injection defense, a second layer after the guardrail. |
| **LLM cites songs by ID** | IDs are easy for code to verify; titles alone can be paraphrased. |
| **Keep the model's own title** in the pick before validating | So the validator can catch "right ID, wrong title" mismatches. |
| **Max 2 generate attempts** (1 try + 1 retry) | One retry with feedback fixes most mistakes; more attempts add latency and cost for little gain. |
| **If the parse call fails, skip Gemini for generate too** | If the API is down or slow, don't wait out a second 20-second timeout. |
| **20-second timeout** | Long enough for a normal call, short enough that a hung call doesn't freeze the UI. |
| **Default model `gemini-3.5-flash-lite`** (changeable via `GEMINI_MODEL`) | Tested live: `gemini-2.5-flash` returned 404 (not available to new users), `gemini-3.8-flash` timed out twice, `3.5-flash` worked but took ~9 s per request, and `3.5-flash-lite` was correct in ~1 s per call. |
| **Fallback uses template explanations**, not the raw scorer reason strings | Still deterministic and built from the same scorer data, but readable. (The original plan said "plain scorer output"; this is a small deliberate change.) |
| **Debug sabotage switch** | Real models rarely fail the check, so you need a way to *show* retry and fallback in a demo. |

### Guardrail and parser details

| Decision | Why |
|---|---|
| 300-character limit | Plenty for a vibe; limits abuse and token cost. |
| Off-topic text → **clarifying question**, not a hard refusal | Friendlier; the user may just need a hint. |
| Fuzzy-match cutoff 0.75 | Catches typos ("jaz", "lofy"). Side effect: "k-pop" → "pop", which is reasonable, so we also added it as an explicit alias. |
| Offline parser **averages** all matched cues | "Rainy study" blends both settings instead of the last word winning. |
| Negation looks 2 words back ("no vocals-heavy pop") | Catches common phrasings without a full grammar parser. |
| Longer terms matched first | "indie pop" isn't misread as "pop". |
| First mentioned genre/mood wins; an explicit mood beats an activity's implied mood | "happy workout" → happy, not intense. |
| Contradictions are **warnings, not blocks** | The user may really want "sad but hyper"; we just explain why results are a compromise. |

### Logging and testing decisions

| Decision | Why |
|---|---|
| Log to a rotating file; console shows only warnings | Full audit trail without cluttering CLI output; the file can't grow forever. |
| Guardrail blocks logged at INFO, not WARNING | A block is expected behavior, not a system problem. |
| Queries truncated to 200 characters in the log | Keeps logs readable if someone pastes a novel. |
| LLM mocked in tests; `conftest.py` blanks the key | `pytest` needs no key, costs nothing, runs in ~4 s, and gives the same result every time. We found the UI tests were secretly calling real Gemini (via `.env`) before this fix. |
| The original demo (`python -m src.main`) left unchanged | Shows the "before" behavior, including the old bugs, for comparison in the write-up. |

---

## 6. Current state

- **All 49 tests pass**, with no API key needed:
  - guardrails: 14
  - offline parser: 9
  - agent: 9
  - Gemini paths (mocked): 10
  - UI: 5
  - original: 2
- **Verified live with your key:**
  - normal request → grounded picks
  - corrupt 1 → caught, retried, fixed
  - corrupt 2 → fell back
  - "capital of France" → clarifying message
  - "sad but hyper, energy 1.5" → clamped + contradiction warning

### How to run

```bash
source .venv/bin/activate
streamlit run app.py                                   # web UI
python -m src.main -q "chill study music"              # CLI, Gemini if key present
python -m src.main -q "chill study music" --offline    # CLI, never calls Gemini
python -m src.main -q "upbeat road trip" --corrupt 1   # demo the retry loop
python -m src.main                                     # original profile demo
pytest                                                 # all tests, no key needed
```

---

## 7. Known limitations (worth mentioning in the model card)

1. **Explanations aren't fact-checked word by word.** The validator checks IDs, titles, count, duplicates, and that an explanation exists. Gemini occasionally adds a phrase the data doesn't support (one run said "without distracting pop elements").
2. **Gemini reorders songs**, so a lower-scored song can appear above a higher one. That's allowed by design, but it can look odd next to the match %.
3. **The offline parser only knows the words in its tables.** Unusual phrasing gets a clarifying question offline, whereas Gemini handles it.
4. **Tiny catalog (28 songs).** Some moods have one song, and requests like "sad" have few good matches.
5. **Contradiction checks are simple rules**, not a model of music.
6. **Keyword safety filters can miss things or misfire**; they're a first layer, not a complete safety system.
7. **No song descriptions yet.** Gemini only sees numbers and labels. The optional `data/song_notes/` would give it richer context.

---

## 8. What's next

- **Phase 4:** README (setup, architecture diagram, sample runs, screenshots), model card update, commit.
- **Stretch:** `eval/run_eval.py`, labeled test queries reporting pass rate, hallucination rate, fallback rate, and a consistency check (same query 3× → how similar are the results).
- **Optional:** `data/song_notes/` knowledge base for richer RAG.

---

## 9. File map

```
app.py                     Streamlit UI
src/
  main.py                  CLI entry point (+ original demo)
  agent.py                 Orchestrator: guard → parse → sanitize → retrieve → generate → check
  llm.py                   Gemini client: parse + grounded generate
  offline_parser.py        Rule-based parser (no key / fallback)
  guardrails.py            Input checks, clamping, label mapping, conflict warnings
  vocab.py                 Synonym tables + label normalization
  validator.py             Grounding check for picks
  recommender.py           Original scorer + retrieve_candidates() (the retriever)
  logging_config.py        File logging setup
tests/
  conftest.py              Blanks the API key for all tests
  test_recommender.py      Original tests
  test_guardrails.py       Guardrail tests
  test_offline_parser.py   Parser tests
  test_agent.py            Pipeline + validator tests (offline)
  test_llm_agent.py        Gemini paths with a fake LLM
  test_app.py              Headless UI tests
.env.example               Settings template (copy to .env)
logs/vibefinder.log        Runtime log (git-ignored)
```
