# 🎧 Model Card: VibeFinder 2.0

## 1. Model Name

**VibeFinder 2.0.** It builds on VibeFinder 1.0, my Music Recommender Simulation from Modules 1–3.

---

## 2. Goal / Task and Intended Use

**Goal.** The listener types what they want in plain English, like "something mellow for a rainy study night, no pop." The system picks songs from a 28-song catalog and explains each pick. Every pick must be a real song from the catalog.

**Who it is for.** It is a portfolio and classroom project. It shows how to use an LLM safely with retrieval, guardrails, and self-checks.

**Intended use.**
- Showing how RAG works: retrieve facts first, then let the AI write from them.
- Showing how to check an AI's answer before a user sees it.
- Learning how scoring, ranking, and guardrails shape results.

**Not intended for.**
- Real music apps with real users or real catalogs.
- Deciding which artists get heard or paid.
- Mood, mental health, or medical advice. If a request mentions self-harm, the app does not recommend music. It points to the 988 crisis line instead.
- Anything other than music. Off-topic requests get a clarifying message.

---

## 3. System Summary

VibeFinder 2.0 has six steps. Each step is a separate file.

| Step | What it does | AI or code? |
|---|---|---|
| 1. Guard | Blocks empty, too-long, unsafe, and prompt-injection input | Code |
| 2. Parse | Turns the request into a profile (genre, mood, energy, …) | **Gemini** (`gemini-3.5-flash-lite`, temperature 0), or a rule-based parser offline |
| 3. Sanitize | Clamps numbers to 0–1, maps words to real labels, flags contradictions | Code |
| 4. Retrieve | Scores all 28 songs and keeps the top 10 | Code (the 1.0 scorer) |
| 5. Generate | Picks *k* of the 10 songs by ID and explains them | **Gemini**, or a template offline |
| 6. Check | Makes sure every pick is a retrieved song with the right title | Code; 1 retry, then fallback |

**The scorer (unchanged from 1.0).** Each song gets a score from 0 to 1.

| Feature | Weight | How it earns points |
|---|---|---|
| Energy | 4.0 | Closer to the target is better |
| Mood | 1.5 | Full points if it matches, else none |
| Valence (cheerfulness) | 1.0 | Closer to the target is better |
| Genre | 0.5 | Full points if it matches, else none |
| Acousticness | 0.5 | Closer to the target is better |

What changed is what goes **into** the scorer. In 1.0, a typed profile went straight in. In 2.0, the profile is cleaned first, and Gemini can only choose genre and mood labels that exist in the catalog.

---

## 4. Data Used

- **Catalog:** 28 songs in `data/songs.csv`, unchanged from 1.0. It has 15 genres (lofi has 6 songs; nine genres have only 1) and 16 moods. There is no "sad" mood.
- **Sent to Gemini:** the user's request text, the list of allowed labels, and the 10 retrieved songs' data. No names, accounts, or listening history.
- **Stored locally:** each request is logged to `logs/vibefinder.log`, including the query text. The log is git-ignored and never leaves the machine.
- **Not included:** lyrics, artist background, language, culture, or popularity.

---

## 5. Strengths

- **No invented songs reach the user.** Every pick is checked against the retrieved list. In testing, every fake song I injected was caught.
- **It understands everyday words.** "Mellow", "rainy night", and "no pop" all work. "Sad" now maps to `melancholic`.
- **It explains bad input instead of hiding it.** Out-of-range numbers are clamped with a warning. Contradictions like "sad but hyper" are flagged.
- **It always answers.** If Gemini is down or slow, the offline path takes over.
- **It is transparent.** Every answer has a step-by-step trace, and every request is logged.

---

## 6. Limitations and Biases

### Biases fixed from 1.0

| 1.0 problem | 2.0 status |
|---|---|
| "Chill" got no credit for "relaxed"; "sad" matched nothing | **Fixed.** Gemini picks from real labels, and a synonym table maps words like "sad" → `melancholic`. |
| Out-of-range targets (energy 1.5) silently gave junk | **Fixed.** Clamped to 0–1 with a visible warning. |
| An empty profile returned songs in CSV order | **Fixed.** The app asks a clarifying question instead. |
| No warning for contradictory requests | **Partly fixed.** Simple rules flag common conflicts. |

### Biases and limits that remain

- **Very calm listeners are still served worst.** The retriever is the 1.0 scorer, and only 1 of 28 songs has energy below 0.2. A sleep or meditation listener still gets weaker matches, and the app does not yet say "nothing fits well."
- **Energy still dominates.** It carries 4.0 of 7.5 total weight, so the right energy can beat the right genre or mood.
- **The catalog is tiny and uneven.** Lofi fans get a full list. Reggae fans get one song.
- **Gemini adds its own bias.** It decides what "mellow" or "workout" means. Those choices come from its training data, which likely reflects mainstream, English-language, Western listening habits.
- **Explanations are not fact-checked word by word.** The checker verifies song IDs and titles, not wording. Gemini sometimes adds details that are not in the data, like "steady tempo" or "comfortable rhythm."
- **Gemini can reorder picks.** A song with a lower score can appear above one with a higher score. This is allowed, but it can look odd next to the match %.
- **The same request can parse differently.** Even at temperature 0, one request gave mood `chill` in one run and `focused` in another.
- **The offline parser only knows its word lists.** Unusual phrasing gets a clarifying question offline.
- **The safety filters are keyword-based.** They catch obvious cases, but reworded harmful requests could slip through, and harmless ones could be blocked by mistake.

---

## 7. Potential Misuse and How It Is Prevented

| Possible misuse | What could go wrong | Prevention in place | What I would add |
|---|---|---|---|
| **Prompt injection** ("ignore previous instructions…") | Hijacking Gemini to reveal its prompt or do something else | Guardrail blocks known patterns before any API call. The request is wrapped in `<request>` tags and labeled as data. Gemini can only reply in a fixed JSON format. | A model-based injection classifier, since keyword lists miss reworded attacks |
| **Using it as a free general chatbot** | Spending the API quota on non-music tasks | 300-character limit. Gemini flags off-topic requests, which get no songs. Output is structured song picks only, not free text. | Per-user rate limits if deployed |
| **Self-harm or crisis messages** | A music bot replying casually to someone in danger | Guardrail detects self-harm language and responds with the 988 crisis line, not music. | Better detection than keywords; human-reviewed wording |
| **Harmful or invented content in answers** | The AI recommending songs that don't exist or making false claims | The validator rejects any song not in the retrieved list. Gemini is told not to invent facts (lyrics, vocals, years). | Checking explanation numbers against the data |
| **Treating it as mood therapy** | Relying on it for emotional support | The model card states it is not for mental-health use. | An in-app note saying the same |
| **Leaking the API key** | Someone else using my quota | The key lives in `.env`, which is git-ignored. `.env.example` has no secrets. | Key restrictions in Google Cloud |
| **Privacy of queries** | Requests may reveal personal feelings | Logs stay local and are git-ignored. Only the request text goes to Google. | An option to turn off query logging |

---

## 8. Evaluation

**Summary.** All 49 automated tests pass with no API key. 9 of 10 manual end-to-end checks with live Gemini passed, and 1 was partial (the same request parsed differently on two days). Every hallucinated song I injected was caught: one retry fixed it, and a double failure fell back safely.

**How reliability is checked.**
1. **Automated tests (pytest):** 49 tests covering guardrails, the offline parser, the full pipeline, the Gemini paths with a fake model (retry, fallback, timeouts, off-topic), and the Streamlit UI.
2. **Validation in the app:** every Gemini answer is checked before it is shown, with one retry and a fallback.
3. **Logging and error handling:** every request is logged. Every Gemini failure is caught, so the app never crashes on an API problem.
4. **Manual end-to-end checks:** see the table in the [README Testing Summary](README.md#testing-summary).

**Not done yet.** A labeled evaluation script that measures pass rate, hallucination rate, fallback rate, and consistency across repeated runs.

### Baseline: VibeFinder 1.0 profile evaluation

This is my original 1.0 evaluation. It still describes how the retriever behaves, because 2.0 uses the same scorer.

I ran nine listener profiles through the recommender and read the top 5 songs for each. Three are ordinary listeners and six are "tricky" ones built to confuse the scoring.

**Profiles tested**

- **High-Energy Pop:** wants happy pop, very energetic, cheerful, not acoustic.
- **Chill Lofi:** wants calm, quiet, mostly acoustic study music.
- **Deep Intense Rock:** wants loud, intense, non-acoustic rock.
- **Sad but Hyper:** asks for a "sad" mood but very high energy (conflicting).
- **Lofi Genre, Metal Everything Else:** says "lofi" but wants everything else to sound like metal (conflicting).
- **Loud Unplugged:** wants energy and acoustic-ness both near the maximum (nearly impossible).
- **Unknown Genre/Mood:** asks for a genre and mood that are not in the catalog.
- **Out-of-Range Targets:** asks for energy 1.5, valence -0.3, acousticness 2.0 (impossible numbers).
- **Empty Profile:** states no preferences at all.

**What I found**

1. **"Gym Hero" keeps showing up.** It appears in the top 5 for seven of the nine profiles. It is a very loud (0.93), non-acoustic pop song, so it scores nearly full marks on energy and genre even when the mood is wrong.
2. **The system never says "I don't know."** For the impossible and empty profiles it still gives five confident-looking answers, with scores of 0.00. *(2.0 now catches the empty and out-of-range cases.)*
3. **Raising the energy weight (2.0 to 4.0) and lowering genre (1.0 to 0.5) changed more than I expected.** Genre fans were hurt the most.
4. **A "sad" mood did nothing,** since no song is labeled "sad." *(Fixed in 2.0: "sad" maps to `melancholic`.)*
5. **Very-low-energy listeners are poorly served.** Only 1 song has energy below 0.2. Their best possible score without a label match is about 0.71, versus 0.94–0.95 for mid- or high-energy listeners. Mid-energy songs like Spacewalk Thoughts and Willow Letters appeared in far more top-5 lists (450 and 417 of 1,200 test profiles) than extreme songs like Iron Tide (30).

**Profile comparisons.** The three ordinary profiles got clearly different, sensible lists. Chill Lofi and Deep Intense Rock shared no songs. The tricky profiles showed the limits. "Loud Unplugged" dropped the acoustic request without saying so. "Lofi Genre, Metal Everything Else" got metal, because energy outweighs genre. "Unknown Genre/Mood" got mid-energy songs with scores that looked good but only meant "close to average." The full profile-by-profile write-up is in [docs/original_readme.md](docs/original_readme.md).

---

## 9. What Surprised Me While Testing Reliability

1. **Temperature 0 did not mean "same answer every time."** I expected the same request to give the same parse. Instead, "mellow rainy study, no pop" gave mood `chill` one day and `focused` the next, which led to different songs. Both answers were valid, but it taught me that repeatability has to be measured, not assumed.
2. **My tests were secretly calling the real API.** The Streamlit app loads my `.env` file, so the UI tests were sending real requests to Gemini. Nothing failed. The only clue was that the tests took 12 seconds instead of 4. Now every test runs with the key blanked out. A passing test suite can still hide a problem.
3. **The model almost never failed the check on its own.** I built a whole retry-and-fallback loop and then could not see it work, because Gemini kept giving valid answers. I had to add a switch that injects a fake song on purpose (`--corrupt`). A safety net you never see working is hard to trust.
4. **When told exactly what was wrong, the model fixed it right away.** Every time the checker rejected a fake song and sent back the error, the second answer passed.
5. **Model availability is a real reliability risk.** The first model I picked (`gemini-2.5-flash`) returned "not available to new users," and a newer one timed out twice. The offline fallback and a configurable model name turned out to be necessary, not just nice to have.
6. **Gemini sometimes made better choices than the scorer.** For a rainy study request it put "Nocturne in Rain" first, though the scorer ranked other songs higher on some features. That is the point of adding an LLM, but it also means the match % no longer explains the order on its own.

---

## 10. Ideas for Improvement

1. **Add an evaluation script** with labeled requests, reporting pass rate, hallucination rate, fallback rate, and consistency across repeated runs.
2. **Add a confidence score.** Show "low confidence: nothing fits well" when the best retrieval scores are low. This would finally fix 1.0's "never says I don't know" problem.
3. **Fact-check explanations.** Make sure any number Gemini quotes matches the song's real data.
4. **Grow and balance the catalog,** especially low-energy songs and single-song genres.

---

## 11. Collaboration with AI

**How I used AI.** I built 2.0 with Claude Code as a coding partner, working in phases:

- **Planning:** it helped me plan, and I made the key decisions: RAG plus a self-check loop as the core features, Gemini as the provider because of its free tier, and the build order.
- **Building:** it wrote most of the code and tests one phase at a time and I verified and tested each change.
- **Checking:** I ran the app and CLI to check results. When I didn't understand something, I asked it to explain.

**A suggestion I accepted: build the offline mode first, before adding Gemini.** The AI suggested starting with guardrails, logging, and a rule-based parser that needs no API key, and only then adding Gemini. I accepted because:
- anyone grading or cloning the project can run it and its tests without a key
- every Gemini failure has a safe place to land

It paid off when the first model I tried was unavailable and another timed out: the app kept working. I also accepted its suggestion to reuse my 1.0 scorer as the retriever instead of adding embeddings, because 28 structured songs don't need vector search.

**A suggestion I changed: when to build the Streamlit UI.** The AI's original plan said to keep the command line first and add a Streamlit UI "only if there's spare time," as an optional extra at the end. I changed that and asked for the UI in the first few steps. I wanted to test every later feature by clicking through real requests, not just reading terminal output. After that, each new piece (Gemini, the retry loop, the debug switch) showed up in the UI as soon as it was built, which made problems much easier to spot.

**A suggestion I did not take: a "song notes" knowledge base.** The AI's plan suggested adding a `data/song_notes/` folder with short written descriptions of each song, genre, and mood, so Gemini would have richer context than just numbers. I left it out because the songs are fictional, so those descriptions would have to be invented, most likely by the AI itself. That causes two problems:
- **It would undo my hallucination protection.** Invented "facts" in the knowledge base would flow straight into Gemini's answers, and my validator checks song IDs and titles, not descriptive text. So the notes could add exactly the kind of unverified detail the whole system is designed to prevent.
- **Scope.** Embedding search would add a new dependency for only 28 songs.

The trade-off is that explanations stay closer to the numbers ("energy 0.18, melancholic mood") and sound a bit drier. I chose verifiable over vivid.

**What I learned about working with AI.** The AI drafts fast, but small mistakes still got into its first drafts:
- a wrong test assertion
- a per-file test count that didn't match the real number
- a README summary saying "10/10 passed" when one check was only partial

Each one was caught by running the code or re-checking the numbers before moving on. In 1.0, I caught the AI miscounting genres (it said 8 had one song; the real number was 9). The habit I keep: check every number that goes into a report, no matter who wrote it.

---

## 12. Personal Reflection

**Biggest learning moment.**
I watched the checker catch a fake song, send the error back to Gemini, and get a correct answer on the second try. That is when RAG made sense to me. The AI does not need to know my data. It needs to be given the right facts, and then something outside the AI has to check that it used them. Every guarantee in VibeFinder 2.0 comes from plain code around the model, not from the model itself: no invented songs, no out-of-range numbers, an answer even when the API is down.

**Fluent is not the same as correct.**
In 1.0, the system showed five confident-looking songs even when every score was 0.00. In 2.0, the AI writes smooth, friendly explanations, which makes it sound even more sure of itself. Most of the time it is right. But it sometimes adds details that are not in the data, like a "steady tempo." A nicer-sounding answer makes a wrong one harder to spot. That is why I show warnings, a trace of every step, and a clear note when the system falls back. The user should be able to see how sure the system really is, not just how sure it sounds.

**Reliability has to be measured.**
I assumed temperature 0 would give the same answer every time. It did not. I assumed my tests were offline. They were not. Both problems were invisible until I looked closely. I also learned to test failures on purpose. The model almost never failed on its own, so I had to build a way to break it before I could trust the safety net.

**Working with an AI coding partner.**
The AI let me build much more than I could have alone in the same time. But speed only helped because I made the key decisions myself and checked each step. When I did not understand something, like how RAG works, I asked it to explain until I could explain it back. A project I cannot explain is not really mine.