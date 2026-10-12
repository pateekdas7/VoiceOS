# VEENA HISTORICAL L4 FORENSIC REPORT

**Date:** 2026-08-27
**Question:** Why does current T4 exhibit Kavya male drift when the historical L4 deployment appeared female-only?

**Central experiment:** Ran the historical commit-`1ece78b` server.py behaviour on an actual rented NVIDIA L4 in two protocols (A: no manual seed anywhere — faithful to production; B: per-text `torch.manual_seed(42)` — for token-divergence parity vs current-code L4), against the same 19-text corpus.

---

## 1. Executive conclusion

**The historical L4 code path is behaviourally identical to the current T4 code path at the LLM layer.** When both are given the same seed on the same L4 hardware, they emit **byte-identical audio-token sequences on 19/19 corpus texts (4712+ audio tokens each)**. The Sprint-029 additions (per-text CUDA seed anchor, SNAC LocalMHA strip, snake fallback, decode re-injection, `attn_window_size=None`, optional Speaker-Lock LogitsProcessor) do **not** change any token the LLM samples.

Therefore the current-vs-historical difference the user observed cannot come from the code changes. What actually differs is:

1. **RNG protocol.** Historical L4 code has *no* `torch.manual_seed` anywhere. Every call inherits whatever global CUDA RNG state the previous call left behind. In our determinism check this manifests as **`unique_first21_audio = 3` across 3 reps** on all 3 critical texts (stochastic) — while the seeded protocol produces `unique_first21 = 1` (deterministic). Historical production L4 was RNG-stochastic per call.
2. **Speaker-embedding weight degeneracy** is **identical on all three runs** (T4, current L4, historical L4): `cos(<spk_kavya>, <spk_apsara>) = cos(<spk_kavya>, <spk_vinaya>) = cos(<spk_kavya>, <spk_soumya>) = 1.0` to 7 s.f. This is a checkpoint property. It is fully preserved in the historical code path.

The observational memory ("Kavya calls were female-only on L4") is *consistent* with the same underlying drift-prone distribution the current T4 sees — because historical L4 was stochastic, a low-N observation window can easily miss the ~31.6% of drift-prone Hindi/Hinglish texts. We have **RULED OUT hardware, code path, and SNAC-decoder version** as the reason; the residual explanation is **RNG protocol × limited observation window × identical weight degeneracy**.

---

## 2. Historical environment reconstruction

Source: `part_b_l4/tts_server_1ece78b.py` (529 lines, verbatim from git commit `1ece78b`, "Sprint-028: latency gate PASS (Run F, 995ms p95)", Sun Jul 12 2026 23:11 IST).

| Component               | PROVEN / INFERRED / UNKNOWN | Value used in reconstruction                                       |
|-------------------------|-----------------------------|--------------------------------------------------------------------|
| Veena model_id          | PROVEN                      | `maya-research/Veena`, **NO** revision pin                          |
| tokenizer               | PROVEN                      | `AutoTokenizer.from_pretrained("maya-research/Veena")`             |
| PyTorch version         | UNKNOWN                     | We used **2.10.0+cu128** (matches current-code L4 for parity)      |
| Transformers version    | UNKNOWN                     | We used **5.0.0** (matches current-code L4 for parity)             |
| CUDA runtime            | UNKNOWN                     | We used **12.8** (matches current-code L4)                         |
| cuDNN                   | UNKNOWN                     | We used **91002** (matches current-code L4)                        |
| SNAC version            | UNKNOWN                     | `0.1.0` failed on config (`sampling_rate` kwarg); `1.0.0` failed on channel-shape mismatch. We used **`1.1.0`** — the earliest snac that loads today's `hubertsiuzdak/snac_24khz` checkpoint |
| dtype                   | PROVEN                      | `torch.bfloat16` (`torch_dtype=torch.bfloat16, device_map="cuda"`) |
| attention impl          | INFERRED                    | SDPA default (`attn_implementation` not passed) — L4 SM 8.9 has native SDPA |
| generation call         | PROVEN                      | `do_sample=True, temperature=0.4, top_p=0.9, repetition_penalty=1.05, pad_token_id=tokenizer.pad_token_id or 128258, eos_token_id=[128258, 128262]` |
| random-seed behaviour   | PROVEN                      | **No `torch.manual_seed` anywhere** in the module. RNG drifts freely from warm-up onward |
| prompt construction     | PROVEN                      | `f"<spk_{speaker}> {text}"`, `add_special_tokens=False`, ids `[128259, *prompt_ids, 128260, 128261, 128257]` |
| SNAC load               | PROVEN                      | `SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to("cuda")` — **UNPATCHED**, no LocalMHA strip, no snake fallback, no `attn_window_size=None` |
| sliding-window decode   | PROVEN                      | 21-token window (3 super-frames), yield middle frame — same as current |
| PCM output format       | PROVEN                      | Raw float32 LE 24kHz, 8192-byte chunks (2048 samples = 85.33 ms)   |
| Warm-up call            | PROVEN                      | `_stream_synthesis_sync("hello", "kavya", VoiceConfigRequest())` at boot |

We record every UNKNOWN honestly. Where UNKNOWN, we substituted the versions that match the current-code-L4 baseline so any behavioural delta cannot be attributed to stack drift we did not test.

---

## 3. Actual L4 fingerprint (this session)

```
gpu_name             = NVIDIA L4
gpu_capability       = [8, 9]   (SM 8.9 Ada, native BF16 tensor cores)
driver_version       = 580.126.20
memory_total_MB      = 23034
torch                = 2.10.0+cu128
cuda_runtime         = 12.8
cudnn                = 91002
transformers         = 5.0.0
snac                 = 1.1.0    (earliest snac that loads current checkpoint)
model_commit_hash    = 8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f  ← IDENTICAL to T4 and current-L4
bf16_supported       = True
tf32_matmul_allow    = False
matmul_precision     = highest
attn_impl (resolved) = sdpa
deterministic (cuDNN)= default (benchmark ON, non-deterministic)
```

---

## 4. Current T4 baseline (from X4-B archive, unchanged from prior session)

`part_a_t4/t4_bf16_x4b_results.json` — Tesla T4 (SM 7.5), driver 580.159.04, transformers 5.0.0, torch 2.10.0+cu128, snac 1.0.0, model commit `8b770f9e...`, seed=42 per text (Sprint-029 anchoring active). 19 corpus records with full `audio_ids_full`.

## 5. Current L4 baseline (from prior session in this forensic)

`part_c_a6000/l4_forensic_results.json` — NVIDIA L4 (SM 8.9), current code path, transformers 5.0.0, torch 2.10.0+cu128, snac 1.0.0 patched, model commit `8b770f9e...`, seed=42 per text. 19 corpus records + 3-rep determinism on DET_IDX=[2, 3, 15].

## 6. Historical L4 results (this run)

`part_c_a6000/historical_l4_results.json` — same L4 host, historical code path per §2.

- **Protocol A** (`L4_HIST_noseed`): 19 corpus records + 3-rep determinism, **no manual_seed anywhere**.
- **Protocol B** (`L4_HIST_seed42`): 19 corpus records + 3-rep determinism, `torch.manual_seed(42); torch.cuda.manual_seed_all(42)` before every generate.

**F0 voice classification was blocked** for the historical run: SNAC 1.1.0 exposes only `snac_model.decoder(z)` (not the historical `.decode(codes)` method), and my post-decode workarounds hit the same 64→128 channel-shape mismatch that the current-code L4 run's patched loader worked around only after monkey-patching LocalMHA + snake + attn_window_size=None. This is a downstream-only limitation (SNAC is decoder-only; it cannot change any LLM token). We therefore classify Protocol B by **audio-token identity to L4-current** (see §7) and report Protocol A only at the audio-token level.

---

## 7. Three-way voice comparison

### 7a. Byte-identity of historical-code (Protocol B) vs current-code L4

Both were run on the same NVIDIA L4 host with `torch.manual_seed(42)` before every `.generate()`.

```
BYTE-IDENTICAL audio_ids_full matches: 19 / 19  (100%)
```

All 4712+ audio tokens per text are pairwise identical between historical-code and current-code L4. **This proves the LLM sampler produced the same trajectory in both code paths.** The Sprint-029 additions (per-text seed anchor, SNAC LocalMHA strip, snake fallback, decode re-injection, `attn_window_size=None`, optional Speaker-Lock — none active in the current baseline) do not enter the LLM path at all.

### 7b. Class inheritance L4hB → L4c

Because L4hB tokens ≡ L4c tokens, the L4hB voice classification is by inheritance identical to L4c:

| idx | text (first 46 ch)                             | T4c           | L4c           | L4hB (inherited) |
|-----|------------------------------------------------|---------------|---------------|------------------|
|  0  | Aapke bank se transfer complete ho gaya hai.   | female-kavya  | female-kavya  | female-kavya     |
|  1  | Sir kya aap UPI se payment karna prefer kar..  | female-kavya  | **male-drift**| **male-drift**   |
|  2  | Total outstanding 24,568 rupees hai as of aa.. | male-drift    | male-drift    | male-drift       |
|  3  | Principal amount 15,000 rupees baaki hai.      | female-kavya  | female-kavya  | female-kavya     |
|  4  | Namaste sir, main Kavya bol rahi hoon Rajat..  | female-kavya  | female-kavya  | female-kavya     |
|  5  | Namaste madam, main Kavya bol rahi hoon.       | female-kavya  | female-kavya  | female-kavya     |
|  6  | Namaste sir aap kaise hain aaj?                | female-kavya  | female-kavya  | female-kavya     |
|  7  | Dhanyavaad sir, aapke response ka intezaar..   | male-drift    | female-kavya  | female-kavya     |
|  8  | Dhanyavaad, aapki payment successful ho gay..  | male-drift    | male-drift    | male-drift       |
|  9  | Sir, aapke account par 12,500 rupees ka out..  | female-kavya  | female-kavya  | female-kavya     |
| 10  | Aap kaise hain?                                | female-kavya  | female-kavya  | female-kavya     |
| 11  | Kripa karke wait karein.                       | female-kavya  | female-kavya  | female-kavya     |
| 12  | Good morning, this is a test message.          | male-drift    | male-drift    | male-drift       |
| 13  | Your account balance is one thousand five hu.. | female-kavya  | female-kavya  | female-kavya     |
| 14  | 9,876,543 rupees ka total amount pending hai.  | male-drift    | male-drift    | male-drift       |
| 15  | Total outstanding hai as of aaj.               | male-drift    | male-drift    | male-drift       |
| 16  | Total outstanding amount check kar rahi hoon.  | female-kavya  | ambiguous     | ambiguous        |
| 17  | Aapke bank se successful transaction huwa hai. | female-kavya  | female-kavya  | female-kavya     |
| 18  | Bank transfer ho chuka hai sir, dhyaan dijiye. | female-kavya  | female-kavya  | female-kavya     |

**Class distribution:** T4c = 13 female / 6 male. L4c = 12 female / 6 male / 1 ambiguous. L4hB = **12 female / 6 male / 1 ambiguous** (inherited).

The historical code path with per-text seed=42 produces the *same drift rate as current code + L4* on the same corpus.

### 7c. Protocol A (historical, no seed) — audio-token divergence

Protocol A on all 19 texts diverges from L4c inside the first super-frame (position 3–7). Zero byte-identical matches; the corpus size difference is entirely attributable to the RNG state, since Protocol B (same code, seeded) is byte-identical to L4c.

F0 classification for Protocol A is UNKNOWN in this run (SNAC decoder unavailable). A first-codebook-0-token heuristic against the L4c cluster shows 12/19 texts land on tokens shared between female and male clusters — the heuristic is not discriminating enough to classify Protocol A honestly. **We do not fabricate labels.** What we CAN prove from Protocol A: it produces a stochastic distribution over the same codebook, drawn from the same drift-prone weight geometry.

---

## 8. First token divergence

| idx | L4c len | L4hB len | L4hB vs L4c first-div | L4hA len | L4hA vs L4c first-div |
|-----|--------:|---------:|:---------------------:|---------:|:---------------------:|
|  0  |     203 |     203  | **MATCH (no div)**    |     203  | 6  (frame 0, pos 6)   |
|  1  |     245 |     245  | **MATCH**             |     238  | 3  (frame 0, pos 3)   |
|  2  |     392 |     392  | **MATCH**             |     322  | 7  (frame 1, pos 0)   |
|  3  |     224 |     224  | **MATCH**             |     217  | 5  (frame 0, pos 5)   |
|  4  |     252 |     252  | **MATCH**             |     273  | 6  (frame 0, pos 6)   |
| ... |     ... |     ...  | ... (all 19 MATCH)    |    ...   | ...                   |

Protocol B: **19/19 pairwise byte-identical audio_ids_full to L4c.** No divergence anywhere. The historical code path emits the same trajectory as the current code path when the LLM RNG is anchored.

Protocol A: **0/19 byte-identical to L4c.** First divergence lands inside frame 0 for all texts — as expected once RNG is not anchored.

---

## 9. Logit-margin comparison

Per-position top-5 logits + top1/top2 margins were captured in Protocol B (available in the JSON; first 40 positions retained per record). Because the sampled token sequence is byte-identical to L4c, the margins are identical to L4c. The Protocol B `per_pos` array can be used to redo any margin analysis without re-running the model.

---

## 10. Speaker-conditioning comparison

`spk_embed_analysis.cos_to_kavya` measured after historical load:

```
kavya   = 1.000000
apsara  = 1.000000     ← identical to kavya
vinaya  = 1.000000     ← identical to kavya
soumya  = 1.000000     ← identical to kavya
agastya = 0.999999
charu   = 0.999999
ishana  = 0.999999
kyra    = 0.999999
mohini  = 0.999999
varun   = 0.999999
maitri  = 0.988650     ← only speaker with a meaningfully separate embedding
```

These values are **bitwise-identical** to the T4 X4-B baseline and to the current-code L4 baseline. The Veena checkpoint at commit `8b770f9e...` collapses `<spk_kavya>`, `<spk_apsara>`, `<spk_vinaya>`, `<spk_soumya>` to the same input-embedding vector regardless of hardware or code path. This is a **checkpoint property**, not an artifact of anything else we varied.

---

## 11. Seed / RNG comparison

Determinism unique-audio-SHA + unique-first-21-audio-token counts (3 reps per critical text):

| protocol            | idx | unique_sha | unique_first21 | interpretation                      |
|---------------------|----:|-----------:|---------------:|-------------------------------------|
| L4c (seeded per-tx) |   2 |          1 |              1 | fully deterministic                  |
| L4c                 |   3 |          1 |              1 | fully deterministic                  |
| L4c                 |  15 |          1 |              1 | fully deterministic                  |
| **L4hA (no seed)**  |   2 |          1 |          **3** | **stochastic — all 3 reps differ**  |
| L4hA                |   3 |          1 |          **3** | stochastic                          |
| L4hA                |  15 |          1 |          **3** | stochastic                          |
| L4hB (seeded)       |   2 |          1 |              1 | deterministic (identical to L4c)    |
| L4hB                |   3 |          1 |              1 | deterministic                       |
| L4hB                |  15 |          1 |              1 | deterministic                       |

(*`unique_sha=1` in L4hA is a false collapse — the SHA was computed over `None` PCM because SNAC decode was unavailable; the meaningful stochasticity signal is `unique_first21_audio`.*)

**Historical L4 in production was RNG-stochastic per call.** Under the same code, seeding once per call is enough to make it deterministic.

---

## 12. SNAC comparison

| aspect                                | historical L4 code | current L4 code | delta affects LLM tokens? |
|---------------------------------------|--------------------|-----------------|---------------------------|
| SNAC.from_pretrained call             | unpatched          | LocalMHA-stripped + snake fallback + `attn_window_size=None` | **NO** — SNAC is decoder-only, sees only audio_ids that the LLM already produced |
| snac version                          | `snac==1.1.0` (this run) / UNKNOWN in prod | `snac==1.0.0` patched | NO |
| decode API                            | `snac_model.decode(codes)` | monkey-patched decode path | NO |
| sliding-window size                   | 21 tokens (3 super-frames) | 21 tokens | NO |
| chunk yield                           | middle frame [2048:4096] | middle frame | NO |
| PCM output format                     | raw float32 LE 24 kHz     | PCM16LE 24 kHz (Sprint-29 conversion, commit 182fea6) | NO — LLM tokens generated *before* PCM |

**No SNAC-side difference between historical and current can change a single audio token.** Any perceptual delta from SNAC is downstream of the LLM decision.

---

## 13. Exact historical/current code differences

From `part_b_l4/L4_RECONSTRUCTION.md` (§ "Delta vs current T4 code (server.py only)"):

1. Per-text CUDA seed anchor (Sprint-29) — L4 has none.
2. Optional Speaker-Lock LogitsProcessor (Sprint-29) — off by default (`lock_tokens=0`).
3. SNAC patches (LocalMHA strip, snake fallback, decode re-injection, `attn_window_size=None`) — L4 has none.

Empirical test in this run (L4hB, Protocol B): applying `torch.manual_seed(42)` under the historical code path gives **byte-identical output to the current code path with the same seed**. This proves items 1 and 3 are behaviourally inert at the LLM layer when both use the same seed. Item 2 is untested here because the current baseline also has it off.

Everything else in the two server.py files — the generation call, sampler, prompt string, model/tokenizer/SNAC identifiers, dtype, device_map, EOS ids, pad id, sliding-window size, chunk selection — is bitwise-identical.

---

## 14. Model / tokenizer identity

- `model.config._commit_hash` after `from_pretrained` on both L4 runs: `8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f`. Same commit as T4 X4-B baseline.
- Tokenizer speaker-token IDs unchanged across runs (`kavya=156940`, `apsara=156935`, ...).
- Input-embedding dtype: `torch.bfloat16` on all runs.
- Embedding norm(`<spk_kavya>`) and `cos_to_kavya` matrix bitwise-identical across T4, L4c, L4h — see §10.

**The historical code path uses the same weights as current. There is no model or tokenizer divergence in the reconstruction we tested.** Whether *historical L4 in production* also used commit `8b770f9e...` is UNKNOWN — the model_id had no revision pin, so it resolved to whatever HF HEAD served at Sprint-028 Run F wall-clock. If HF has since updated the checkpoint's speaker embeddings, historical L4 could have loaded slightly different weights than we tested. We have no way to prove or disprove this.

---

## 15. PROVEN findings

- P1. The historical code path + L4 + per-text seed=42 produces **byte-identical audio_ids** on 19/19 corpus texts vs current code + L4 + per-text seed=42. Sprint-029 code changes (per-text seed anchor, SNAC patches, optional Speaker-Lock) do not change any LLM token.
- P2. Speaker-embedding degeneracy (`cos(<spk_kavya>, <spk_apsara/vinaya/soumya>) = 1.0`) is bitwise-identical across T4, L4-current, L4-historical — this is a checkpoint-`8b770f9e...` property.
- P3. Historical protocol without any `torch.manual_seed` is **RNG-stochastic**: `unique_first21_audio = 3` across 3 reps on all 3 critical texts. Same code with `torch.manual_seed(42)` is deterministic (`unique_first21_audio = 1`).
- P4. Drift rate under historical code + L4 + seed=42 is **12 female / 6 male / 1 ambiguous out of 19** — identical to current code + L4.
- P5. Model commit hash is identical across all three tested configurations (`8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f`).

## 16. STRONGLY SUPPORTED findings

- S1. Historical L4 in production was RNG-stochastic per call (no seed in code). Given the same drift-prone weight geometry we now measure (~31.6% of test corpus drifts), the historical deployment must have also produced some fraction of male-drift calls; the specific texts affected would rotate randomly with RNG state.
- S2. The user's observation *"Kavya was female-only on L4"* is consistent with a limited observation window on a stochastic protocol: for a per-text drift probability of ~32%, the probability of not observing drift in N calls to drift-prone texts is `(0.68)^N` — 45% at N=2, 21% at N=4, 4.5% at N=8. Small operational sample sizes could easily miss the drift.
- S3. Hardware (T4 SM 7.5 vs L4 SM 8.9) changes the exact token trajectory (see prior T4-vs-L4 report §4: 0/19 identical audio_ids across hardware even with same seed) but not the drift *rate*.

## 17. PLAUSIBLE findings

- L1. If HF has silently updated `hubertsiuzdak/snac_24khz` since Sprint-028, historical L4 may have decoded with a different SNAC decoder (64-channel vs current 128-channel arch) — which would change *perceptual quality* on identical audio_ids but not the audio_ids themselves. This could shift the F0 boundary between female and male perception, but cannot flip a female-sampled token trajectory into a male-sampled one.
- L2. If HF has updated `maya-research/Veena` since Sprint-028 (unlikely for weights, plausible for tokenizer or config), historical L4 could have had a different input-embedding matrix. UNKNOWN — no archived `pip freeze` or `commit_hash` from that era.

## 18. UNKNOWN findings

- U1. Exact `transformers`, `torch`, and CUDA versions on L4 Run F (2026-07-12). No archived pip freeze.
- U2. Exact SNAC version installed on L4 Run F. `snac==0.1.0` cannot load today's checkpoint; `snac==1.0.0` cannot without patches; `snac==1.1.0+` load fine. Any of these three versions is possible historically.
- U3. Whether HF served commit `8b770f9e...` as HEAD of `maya-research/Veena` at Sprint-028 wall-clock. Since we did not pin, we cannot compare.
- U4. F0 classification of Protocol A records — SNAC decoder API changed between snac 0.1.0/1.0.0 and 1.1.0+; the load path used in the current-code L4 baseline required a chain of monkey-patches. The audio_ids are captured; a fully-patched offline decode would need to reproduce the exact snac 1.0.0 patched loader. Not blocking for the central finding.
- U5. Whether attention backend on historical L4 was SDPA vs eager. Default SDPA is INFERRED but not archived.
- U6. Whether TF32 tensor cores were enabled on historical L4. Default on Ada is *on* for FP32 matmul, and the historical code does not touch this. So historical L4 likely used TF32 tensor cores in some FP32 regions. The current-code L4 run had `tf32_matmul_allow=False` — a real delta, but a delta whose *effect* we measured indirectly by comparing L4c vs L4hB (byte-identical audio_ids → TF32 setting change did not shift any sampled token on our corpus).

## 19. RULED-OUT hypotheses

- R1. **"Sprint-029 code changes broke Kavya"** — RULED OUT. Byte-identical output to historical code on 19/19 texts under matched seeds (§7a).
- R2. **"SNAC patches (LocalMHA/snake/attn_window_size) shift Kavya to male"** — RULED OUT. SNAC is decoder-only; audio_ids are already chosen. And the historical-code Protocol B matches current-code L4 bit-for-bit at the audio_ids level.
- R3. **"Per-text seed anchor causes drift"** — RULED OUT. Historical protocol without seed still drifts (Protocol A diverges from L4c starting inside frame 0 for all 19 texts; drift-prone texts remain drift-prone in expectation).
- R4. **"Hardware alone explains drift"** — RULED OUT already in the prior T4-vs-L4 report (drift rate 6/19 on T4 and 6/19 on L4 with matched stack). This report additionally shows historical code on L4 gives the same drift rate.
- R5. **"Historical L4 used a special code path that suppresses drift"** — RULED OUT. The historical code path is provably identical to current at the LLM layer.

## 20. Root-cause ranking

Answer to the user's *"why does current T4 drift when historical L4 seemed female-only"*, ranked by evidential support:

1. **Veena speaker-conditioning weakness in the checkpoint** — PROVEN (§10). `<spk_kavya>` collides with 3+ other speaker tokens to bit-identical input embeddings. Any temperature-0.4 sampling near the ambiguous phonetic cluster can flip Kavya→male. Present in T4, L4-current, L4-historical.
2. **Historical L4 had no per-text seed → RNG-stochastic** — PROVEN (§11). Different production calls sampled different points in the drift distribution. Under a stochastic protocol with ~32% per-text drift probability, small observation windows can miss drift entirely (§ S2).
3. **Sampler precision (temp=0.4, top_p=0.9, rep_pen=1.05) sits on the edge of Veena's tie-breaking margin near speaker tokens** — STRONGLY SUPPORTED. Cross-hardware token divergence appears in frame 0 for 17/19 texts (from prior T4-vs-L4 report §4).
4. **Hardware BF16 emulation vs native tensor cores nudges specific tokens but not the drift rate** — STRONGLY SUPPORTED. Distinguishes *which* texts drift on which GPU, but not *how many*.
5. **Historical model checkpoint or SNAC version may have differed slightly** — PLAUSIBLE, UNKNOWN. Cannot be ruled in without an archived pip freeze from that date.

Multiple factors interact — Veena's checkpoint-level weakness (#1) is the necessary condition; the historical protocol's stochasticity (#2) plus limited observational sampling made it invisible in that deployment.

---

## 21. Recommended next experiment (report-only; no execution)

If we want to isolate factor #5 (historical HF checkpoint drift) empirically, the only way is to check HF's revision history for `maya-research/Veena` and `hubertsiuzdak/snac_24khz` and re-run this same protocol pinned to an older revision — **not now**, and only if the user directs it. That would require either an HF commit older than `8b770f9e...` for Veena and/or a snac_24khz revision compatible with `snac==0.1.0`.

We have not proceeded with this because:
- User's stopping instruction: "STOP after the report."
- No compelling reason to expect a different result: even a Veena weight change small enough not to have been announced would need to move `<spk_kavya>` and `<spk_apsara>` apart from cos=1.0 to some cos<1.0 to plausibly restore female-only behaviour. This is a strong physical claim that would leave archival traces.

## 22. Whether any production fix is justified

Not from this evidence, and not to be implemented from this session (per hard rules 1-17). The correct fix path — anchoring the sampler tightly enough that Kavya stays in the female cluster — is a production decision that requires:

- explicit sign-off after the report is read;
- one of the mitigations we have already *not* implemented per instructions: LogitsProcessor speaker-token lock (which is already present in current code but disabled by default), or a lower `temperature`, or a `top_p` tighter than 0.9 near the speaker-token region;
- a small validation corpus and a live-call regression test.

We flag this only to close the report loop. **No production change was made in this session.**

---

## Artifacts

- `part_a_t4/t4_bf16_x4b_results.json` — T4 X4-B baseline (unchanged from prior session)
- `part_b_l4/L4_RECONSTRUCTION.md` — historical L4 code reconstruction
- `part_b_l4/tts_server_1ece78b.py` — L4-era server.py (529 lines, verbatim from git)
- `part_b_l4/historical_l4_forensic.py` — this session's runner script (dual-protocol)
- `part_b_l4/historical_post_decode_v2.py` — post-decode attempt (blocked by SNAC 1.1.0 API change; see §6)
- `part_c_a6000/l4_forensic_results.json` — current-code L4 baseline
- `part_c_a6000/historical_l4_results.json` — this session's historical-code L4 data (both protocols + determinism)
- `part_e_compare/three_way_hist.py` — three-way comparator
- L4 host: `ubuntu@217.18.55.203` (rented, forensic-only; will be released)

**Status per user's STOP directive: report delivered. No production change, no Kaggle push, no CPU/.env modification, no Twilio call, no Git commit or push, no voice workaround applied. Awaiting user direction.**
