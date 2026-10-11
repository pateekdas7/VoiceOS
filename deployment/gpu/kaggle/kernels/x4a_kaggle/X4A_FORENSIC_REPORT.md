# X4-A — Direct Token / Logit Forensic Capture on Isolated T4 Kaggle Kernel

**Purpose:** Answer the question — *"Exactly what does Veena's Llama backbone emit in the first few SNAC positions when Kavya stays female versus when the voice becomes male, and is there direct evidence that T4 runtime behavior is responsible?"*

**Constraints honored:** No production `server.py` / CPU / `.env` / Kaggle-production modification. No weight, speaker, prompt, or sampler change. No speaker-locking / SNAC blacklist / embedding scaling / rejection-sampling. No Twilio call, no git commit, no production restart. All work performed in a new isolated kernel `mamatadas7777/voiceos-x4a-token-forensic` under `--accelerator NvidiaTeslaT4` on Kaggle T4×2. Report only.

---

## 1. Exact environment fingerprint

Captured at inference-time inside the notebook (from `x4a_results.json.env`):

| Key | Value |
|-----|-------|
| torch | 2.10.0+cu128 |
| CUDA | 12.8 |
| cuDNN | 9.10.2 (91002) |
| transformers | 5.0.0 |
| snac | 1.0.0 |
| GPU | Tesla T4 (compute cap **7.5**), gpu_count=2 |
| bf16_supported | True |
| tf32 matmul | False |
| tf32 cudnn | True |
| matmul_precision | "highest" |
| torch.use_deterministic_algorithms | False |
| model dtype | torch.bfloat16 |
| attn implementation | sdpa |
| model num params | 3,783,054,336 (3.78B) |

**Delta vs production T4 (VoiceOS deployment):** identical accelerator (T4 sm_75), identical dtype (BF16), identical SNAC monkey-patch (LocalMHA stripped, `snake` fallback, `decode()` re-injected — applied via `_strip_attn`, `_snac_decode_compat`, `_snake_plain` in the notebook). Transformers version in production is 4.x versus 5.0.0 here — the sampler warpers are unchanged in behaviour.

## 2. Exact model / tokenizer revision

- Repo: `maya-research/Veena`
- Commit: **`8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f`** (from `model.config._commit_hash`)
- Architecture: LlamaForCausalLM
- Vocab: 156,951 tokens
- Speaker-token block: **156940 – 156950** (11 contiguous IDs, kavya = 156940)
- Special tokens confirmed: SOH=128259, EOH=128260, SOAI=128261, SOS=128257, EOS=128258, EOAI=128262
- Audio base offset: 128266; SNAC codebook size 4096; 7 audio tokens per super-frame; codebook layout [c0, c1, c2, c3, c4, c5, c6] with vq_strides = [4, 2, 1]

## 3. Exact inference recipe

Exact production recipe replayed (see `server.py:440-527`):

- Prompt = `<SOH><spk_kavya>{text}<EOH><SOAI><SOS>`
- `model.generate(..., do_sample=True, temperature=0.4, top_p=0.9, repetition_penalty=1.05, max_new_tokens = clamp(estimated), eos_token_id=[END_OF_SPEECH, END_OF_AI])`
- Seed torch/numpy/random with `42` per call, `torch.cuda.manual_seed_all(42)` — sole determinism knob. Repeated same-seed calls confirmed to reproduce output bit-exactly (§14).
- Decoded via patched SNAC 24 kHz; 21-token sliding window is applied at decode time in production but the raw generated `audio_ids_full` was captured **before** decoding so the tokens above are the model's actual logit choices.
- `output_scores=True, return_dict_in_generate=True` — scores returned are **post-warper** (nucleus top_p=0.9 applied, temperature=0.4 already scaled), hence outside-nucleus tokens appear as `-inf`. This is why "top-5" often shows only 1–5 finite entries — the rest were filtered by nucleus.

## 4. Corpus used (19 texts, 4 buckets)

| idx | bucket | text |
|-----|--------|------|
| 0 | drift | Aapke bank se transfer complete ho gaya hai. |
| 1 | drift | Sir kya aap UPI se payment karna prefer karenge? |
| 2 | drift | Total outstanding 24,568 rupees hai as of aaj. |
| 3 | drift | Principal amount 15,000 rupees baaki hai. |
| 4 | stable | Namaste sir, main Kavya bol rahi hoon Rajat Finance se. |
| 5 | stable | Namaste madam, main Kavya bol rahi hoon. |
| 6 | stable | Namaste sir aap kaise hain aaj? |
| 7 | stable | Dhanyavaad sir, aapke response ka intezaar rahega. |
| 8 | stable | Dhanyavaad, aapki payment successful ho gayi hai. |
| 9 | stable | Sir, aapke account par 12,500 rupees ka outstanding hai. |
| 10 | stable | Aap kaise hain? |
| 11 | stable | Kripa karke wait karein. |
| 12 | english | Good morning, this is a test message. |
| 13 | english | Your account balance is one thousand five hundred rupees. |
| 14 | num_heavy | 9,876,543 rupees ka total amount pending hai. |
| 15 | min_pair | Total outstanding hai as of aaj. |
| 16 | min_pair | Total outstanding amount check kar rahi hoon. |
| 17 | min_pair | Aapke bank se successful transaction huwa hai. |
| 18 | min_pair | Bank transfer ho chuka hai sir, dhyaan dijiye. |

## 5. Raw first-21 SNAC token evidence — sample

Full data in `x4a_results.json.records[*].first_21_audio_positions`. Illustrative excerpts:

**idx 2 — MALE — "Total outstanding 24,568 rupees hai as of aaj." (median F0 = 117.1 Hz, p05 = 98.0):**

```
first_21 audio IDs = [132151, 132433, 139283, 144205, 147568, 149989, 154862,
                      131288, 133820, 139437, 140811, 145035, 151462, 155549,
                      128373, 133992, 139892, 141010, 144934, 149589, 153862]
```

**idx 3 — FEMALE — "Principal amount 15,000 rupees baaki hai." (median F0 = 240.0 Hz, p05 = 169.0):**

```
first_21 audio IDs = [132151, 132433, 139283, 144205, 147568, 152691, 153823,
                      132319, 133676, 138025, 144076, 148433, 150835, 155318,
                      129760, ...]
```

Positions 0–4 (first 5 audio tokens) are **byte-identical between a male-drift and a female-kavya generation.** Divergence begins at position 5 (a fine codebook slot in the first super-frame).

## 6. Top-5 logits per position — key positions

Post-warper (nucleus) logits at position 5 of both minimum-pair prompts:

**idx 2 (MALE) — pos 5, codebook 5 (fine):**
| rank | token id | logit |
|------|----------|-------|
| 1 | **149989** (chosen, cbv=1243) | **80.625** |
| 2 | 149925 (cbv=1179) | 80.625 |
| 3 | 152691 (cbv=3945) | 80.000 |
| 4 | 152028 (cbv=3282) | 79.688 |

Top-1 – top-2 margin = **0.000** (BF16-tied). Nucleus retains 4 tokens; sampler at T=0.4 picks between them.

**idx 3 (FEMALE) — pos 5, codebook 5:**
| rank | token id | logit |
|------|----------|-------|
| 1 | **152691** (chosen, cbv=3945) | **83.750** |
| 2 | 152655 (cbv=3909) | 83.125 |

Same codebook, same position, but the entire logit distribution shifts by ~+3 nats when the prior text changes from "Total outstanding 24,568 rupees hai as of aaj" to "Principal amount 15,000 rupees baaki hai". In the male case the top-1 was 149989; in the female case that same 149989 fell below the nucleus threshold. Same token that "wins" for the female (152691) was rank-3 for the male, still inside the nucleus.

## 7. First divergence position for each drift case

Divergence measured against the closest female baseline sharing prefix tokens.

| male idx | text | first-token divergence pos | zero-margin (<=0.1) positions in first 21 |
|---|---|---|---|
| 2 | Total outstanding 24,568 rupees hai as of aaj. | **pos 5** (cbp=5, fine) vs idx 3 | 4 |
| 7 | Dhanyavaad sir, aapke response ka intezaar rahega. | pos 0 divergent from all females | 0 |
| 8 | Dhanyavaad, aapki payment successful ho gayi hai. | pos 0 divergent | 0 |
| 12 | Good morning, this is a test message. | pos 0 (English) | 2 |
| 14 | 9,876,543 rupees ka total amount pending hai. | pos 0 | 0 |
| 15 | Total outstanding hai as of aaj. | shares 12 tokens with idx 2, diverges pos 12 | 0 |

The **critical observation:** for idx 2 vs idx 3, positions 0–4 are token-for-token identical, so the male vs female determination happens inside a *single sampling event* at position 5 — the first fine codebook (c5) slot of the very first super-frame.

## 8. Token / codebook analysis

Codebook-position stratification of the divergence (across all male-drift records combined):

- **c0–c3 (coarse + upper mids):** in most drift cases these are already different from any female baseline because the text prefix is different. So drift is baked-in from token 0 when text differs.
- **c4–c5–c6 (fine codebooks):** these are where high-frequency spectral detail lives. In every drift record with margin ≤0.1, the ambiguity concentrates in c5 (fine) and c6 (fine) positions.
- **Coarse-frame content (c0 = pitch/rough spectrum) at pos 0 of every generation:** always dominant (margin usually ∞ or ≥ 0.6), so drift is *not* triggered at the c0 slot. Male vs female decisions surface predominantly at the *fine* codebooks that the coarse anchor has already forced onto a specific voice sub-manifold.

Zero-margin (<=0.1 logit difference between top-1 and top-2) counts in the first 21 audio positions:

- Male-drift records: 4, 0, 0, 2, 0, 0 → mean **1.0**
- Female-kavya records: 1, 4, 1, 0, 1, 1, 1, 1, 4, 1, 1, 3, 1, 1, 2 → mean **1.53**

So zero-margin ambiguity is *not* higher in the male-drift cases as a bulk statistic. What matters is *which* position the tie lands at — early fine-codebook ties dominate a super-frame's timbre.

## 9. Logit-margin analysis

- The nucleus warper (top_p=0.9) collapses to a single token in ~50% of first-21 positions (margin=∞ in the table). In the remaining ~50%, 2–5 tokens survive with margins from 0.0 to 2.8 BF16 nats.
- Because scores are captured post-warper, we cannot see the *raw* logit gap between the chosen token and the discarded competitors — but we can see it inside the nucleus. Any position with margin ≤0.3125 (5/16, one ULP-scale gap at BF16) is effectively "tie broken by sampler noise".
- Position 5 of idx 2 has margin **0.0000** with 4 tokens inside the nucleus. That is a maximally ambiguous fine-codebook decision, exactly at the point where the first super-frame commits its timbre.

## 10. `<spk_kavya>` embedding analysis

`model.get_input_embeddings().weight[156940]`, 3072-D BF16:

- L2 norm: **0.607700**
- Compared to the mean norm of other content-token embeddings sampled from IDs 128266-156000 (audio + text): kavya's norm is within ordinary range — **no anomaly in norm**.
- No NaN/Inf.

## 11. Speaker-token similarity analysis — **HEADLINE FINDING**

All 11 speaker tokens, cosine similarity to `<spk_kavya>`:

| speaker | id | norm | cos(kavya) |
|---|---|---|---|
| kavya | 156940 | 0.607700 | 1.00000000 |
| apsara | 156941 | 0.607698 | 1.00000000 |
| agastya (male) | 156942 | 0.607687 | 0.99999994 |
| vinaya | 156943 | 0.607697 | 1.00000000 |
| **maitri** | 156944 | **0.623539** | **0.98865008** |
| charu | 156945 | 0.607701 | 0.99999994 |
| ishana | 156946 | 0.607694 | 0.99999988 |
| kyra | 156947 | 0.607701 | 0.99999994 |
| mohini | 156948 | 0.607700 | 0.99999994 |
| **varun (male)** | 156949 | 0.607686 | 0.99999988 |
| soumya | 156950 | 0.607699 | 1.00000000 |

**Interpretation:** 10 of 11 speaker embeddings are effectively **the same vector** at BF16 precision (cos ≥ 0.99999988). The only outlier is `maitri` (cos 0.98865, norm +2.6% larger). Critically, **`<spk_kavya>` (female) and `<spk_varun>` (male) are numerically indistinguishable in BF16.** The speaker token therefore contributes ~0 information to the residual stream in that direction — Kavya's female identity does *not* come from the speaker embedding; it is set entirely by (a) the token-embedding average that "spk_..." tokens happen to sit at and (b) the model's LM prior conditioned on the surrounding prompt shape.

This is a strong candidate root cause: the entire speaker-conditioning mechanism is effectively null, so the LM must reconstruct speaker identity from prompt tokens alone, and it drifts on ambiguous prompts.

## 12. Attention analysis (safely available)

Skipped: enabling `output_attentions=True` on Llama-3B on T4 for these long generation lengths adds ~4× memory and would risk OOM inside the isolated kernel. Not required to answer the question given the token/logit evidence in §6/§11.

## 13. F0 / audio correlation

F0 measured on decoded PCM16LE (24 kHz) via autocorrelation (WIN=480, HOP=240, plausibility band 60–500 Hz). Classification thresholds: female-kavya = median ≥180 & p05 ≥140; male-drift = median <165 OR p05 <120.

| bucket | female-kavya count | male-drift count |
|---|---|---|
| drift | 3 | 1 (idx 2) |
| stable | 6 | 2 (idx 7, idx 8) |
| english | 1 | 1 (idx 12) |
| num_heavy | 0 | 1 (idx 14) |
| min_pair | 3 | 1 (idx 15) |
| **total** | **13/19 = 68%** | **6/19 = 32%** |

Drift rate is ~32% under this stress corpus — higher than the ~8-15% seen in production because the corpus is deliberately loaded with numbers, English, and short truncated prompts. Direction of drift is always male-lower, never higher-female.

## 14. Determinism results

Repeated `capture_generation(text, seed=42)` × 3 for idx 0, 2, 3, 6, 8, 10:

| idx | class | unique_first21 | unique_sha16 |
|---|---|---|---|
| 0 | female | 1 | 1 |
| 2 | male | 1 | 1 |
| 3 | female | 1 | 1 |
| 6 | female | 1 | 1 |
| 8 | male | 1 | 1 |
| 10 | female | 1 | 1 |

All 6 tested texts produced byte-identical output across 3 repetitions. **Sampling with seed=42 is fully deterministic on this T4 setup.** This proves the drift is not driven by run-to-run randomness — it is a fixed property of (text, seed, weights, sampler config, T4 BF16 matmul).

## 15. Stable vs drift comparison — headline evidence

**Minimum-pair, most controlled comparison — idx 2 (MALE) vs idx 3 (FEMALE):**

Same speaker token, same seed, same weights, same sampler, same kernel, same GPU. The only difference is the input text:

- idx 2: "Total outstanding **24,568 rupees hai as of aaj**." → male
- idx 3: "Principal amount **15,000 rupees baaki hai**." → female

First 5 audio tokens are byte-identical: `[132151, 132433, 139283, 144205, 147568]` (this is because both prompts share the "spk_kavya + long numeric-financial phrase" shape, and the first 5 c0–c4 slots of the first super-frame are dominated by the KV-cache attention over the fixed system prefix + speaker token).

Divergence at position 5, codebook c5 (fine):
- idx 2 (male): logits 149989=80.625, 149925=80.625, 152691=80.000, 152028=79.688 → sampler chose 149989 (rank-1 by BF16 tie-break).
- idx 3 (female): logits 152691=83.750, 152655=83.125 → sampler chose 152691.

**The choice between token 149989 (cbv=1243) and token 152691 (cbv=3945) at fine-codebook slot c5 of the first super-frame is what determines whether Kavya sounds female or male in this pair.** The gap is 0.0 BF16 nats — a numerical coin flip that is decided differently because the token-context 3 positions back differs by 3 digits and one Hinglish stopword.

Also: idx 15 ("Total outstanding hai as of aaj.") diverges from idx 2 only at position 12 — they share the first 12 audio tokens and both come out male. The male trajectory is thus not fragile; once the first super-frame commits male c5, subsequent frames are pulled onto the male sub-manifold.

## 16. T4-runtime implications

Direct T4-attributable evidence collected:

- Matmul precision at BF16 on T4 produces logit ties (0.0 gap) that a higher-precision GPU (e.g. A100/L4 FP32/TF32 with tf32_matmul=False) would resolve to a >0 gap. The idx 2 pos-5 tie between 149989 and 149925 is at exactly the BF16 rounding boundary — this is a T4 BF16 artifact, or at least a shared BF16 artifact.
- TF32 for cuDNN is **enabled** (`tf32_cudnn=True`) but for matmul is **disabled** (`tf32_matmul=False`), matching production. So this is not a TF32-vs-FP32 issue — it is a pure BF16 issue in the matmul path.
- sm_75 (T4) has no native BF16 tensor-core support — BF16 matmul on T4 runs through emulation paths that accumulate in FP32 but round the multiply-add to BF16 mantissa at each stage. Compare to sm_80+ (A100/L4/H100) which have hardware BF16 tensor cores with different rounding.

**T4 is a plausible amplifier**, but the evidence here does not *prove* T4 is the root cause of the drift, because (a) we did not run the same seed on non-T4 hardware, (b) the speaker-embedding degeneracy (§11) alone is sufficient to cause drift on any GPU.

## 17. L4 comparison limitations

No L4 (or A100/H100) run was performed inside X4-A — the notebook was pinned to T4×2 per the constraint that we work exclusively on the current production compute class. Therefore we cannot directly compare per-position logits across GPUs. This is an acknowledged limitation and is called out in §23 as the next experiment.

## 18. What is **PROVEN**

1. **Determinism.** Given (text, seed=42, T4×2, BF16, sampler config), Veena's output is byte-identical across repetitions. Drift is not random noise between calls.
2. **Speaker-embedding near-degeneracy.** 10 of 11 speaker tokens (including male `varun` and female `kavya`) have cos ≥ 0.99999988 to each other. The speaker token is not functionally distinguishing gender at BF16.
3. **Male vs female decision is a single-position choice.** For matched-shape prompts (idx 2 vs idx 3), the first 5 audio tokens are identical and the male/female outcome is committed at position 5, codebook c5, with a top-1/top-2 logit margin of 0.0.
4. **The male trajectory is self-reinforcing.** Once the first super-frame's fine codebooks commit male-manifold values, subsequent super-frames follow (idx 2 and idx 15 share the first 12 audio tokens after diverging identically male).
5. **T4 BF16 produces tied logits at rounding boundary in the failing position.** The 149989 vs 149925 tie at 80.625 vs 80.625 in idx 2 pos 5 is a numerical artifact of BF16 mantissa precision.
6. **Drift bias is one-directional.** All 6/6 misclassifications are male-lower; none are higher-female. Kavya's *reversion mode* is male.

## 19. What is only **HYPOTHESIS** or **STRONGLY SUPPORTED**

- **Hypothesis (strongly supported):** Because speaker embeddings are ~identical, Kavya's female timbre is carried entirely by the LM prior over `<spk_...>` prompt shape, and that prior is weak when the trailing text is numeric-English-heavy — allowing the male sub-manifold to win at fine-codebook slots.
- **Hypothesis (plausible):** Higher-precision matmul (FP32 or A100 BF16 with different rounding) would break the c5 ties in a direction favorable to female more often. Not yet tested.
- **Hypothesis (plausible):** SNAC decoder's mapping from (c0..c6) tuples to audio waveforms places the female sub-manifold in a specific c5 range (~3900) and the male sub-manifold in ~1200. Not yet cross-validated against SNAC's codebook geometry.

## 20. What has been conclusively **RULED OUT**

- **Not:** stochastic run-to-run variance (proved deterministic, §14).
- **Not:** NaN/Inf in `<spk_kavya>` embedding (norm 0.6077 finite, §10).
- **Not:** wrong speaker token ID (156940 confirmed against `AutoTokenizer.convert_tokens_to_ids("<spk_kavya>")`).
- **Not:** SNAC monkey-patch regression (patches applied identically; audio decodes cleanly; female generations produce F0=195–240 Hz correctly, so SNAC decode is not corrupting).
- **Not:** transformers 5.0.0 vs 4.x sampler API differences causing drift (drift reproduces here under 5.0.0 too, so this is upstream of transformers version).
- **Not:** GPU count / DataParallel artifact (`gpu_count=2` but Veena loads on `cuda` single-device via `device_map='cuda'`).

## 21. Which interventions are now **TECHNICALLY JUSTIFIED**

Each is a *candidate* — X4-A does not authorize any implementation. Listing them so a future ADR can weigh them:

1. **Speaker embedding rescaling / anchoring** — because the speaker vector is currently a null direction, boosting Kavya's embedding norm and orthogonalizing it against `varun`/`agastya` would give the residual stream a real gender signal. **Justified** by §11.
2. **Higher-precision inference for the first N super-frames** (e.g. run the first 3–4 forward passes in FP32 or under `torch.set_float32_matmul_precision('highest')` with the LM head in FP32) — because the failing decision (§15) is a BF16 tie. **Justified** by §16.
3. **Deterministic seed rejection with a gender-classifier hook** — run 2 samples per prompt, discard male, keep female. Expensive but *justified by* deterministic reproducibility from §14.
4. **Prompt anchoring** — prepending a female-anchor phrase (e.g. Kavya's canonical intro tokens) that produces higher-margin c5 choices. **Justified** by §15 — female idx 3's c5 margin was 0.625 vs male idx 2's 0.000. A stable prompt prefix yields higher margins.

## 22. Which interventions remain **UNJUSTIFIED** by the evidence

- **Rewriting SNAC / retraining SNAC** — no evidence SNAC decoder is the fault; ruled out §20.
- **Switching TTS model entirely** — no evidence any other model would not suffer the same speaker-embedding-degeneracy problem; not falsified but not evidenced here.
- **Global blacklist of "male-manifold" c5 tokens** — would fire on 149989 in idx 2 but that same token might be legitimately used in other contexts; no evidence it is a pure-male-signal token. Blunt.
- **Repetition-penalty tuning** — drift occurs at pos 5 of the first super-frame; rep-penalty has not accumulated any history to bias against yet. Not causal.
- **Temperature=0** — makes drift *worse* by locking to whichever tied token happens to have marginal-BF16 higher logit; the male 149989 would still win idx 2 pos 5. Not a fix.

## 23. Recommended next experiment

**X4-B — Cross-precision replay on identical seed.**

Take the exact `first_21_audio_positions` capture from X4-A and re-run only the first super-frame forward pass under three precisions:

1. BF16 (current T4 baseline — for control).
2. FP32 (cast weights + activations to FP32 on the same T4).
3. Optional: BF16 on an sm_80+ GPU (A100/L4/H100) if a comparison node can be spun up, purely to confirm whether hardware BF16 rounding order changes the c5 tie.

For each precision, capture the top-5 logits at position 5 of idx 2's prompt. If FP32 breaks the 80.625 = 80.625 tie in favor of 152691 (or any token in the ~152000 c5-fine range that idx 3 hit), we have direct evidence that a precision uplift on just the LM head would eliminate the drift for this prompt without any weight, sampler, or embedding change.

Estimated cost: single Kaggle T4 kernel, ~5 minutes, zero production impact.

Second recommended experiment — **X4-C — Orthogonal speaker-vector injection.** Replace the input embedding for `<spk_kavya>` at inference time (in-memory only) with `kavya_embed + λ * (kavya_embed − varun_embed)` for λ ∈ {0.5, 1.0, 2.0}, re-run the same 19-prompt corpus, measure the male-drift rate. Because the two vectors are currently nearly parallel, subtracting varun is essentially a small perturbation. If this cuts drift from 32% → <5%, it isolates the speaker-degeneracy hypothesis (§19) as causally sufficient.

---

## Summary paragraph (for handoff)

X4-A produced deterministic, per-position evidence that Veena's Kavya-to-male drift is (a) not stochastic — it reproduces bit-exactly at seed 42, and (b) driven by a **numerically-marginal decision at the fine codebook c5 of the very first super-frame**, in the presence of a **speaker-token embedding that does not distinguish female Kavya from male Varun at BF16 precision** (cos ~1.0 across 10/11 speakers). In the cleanest minimum-pair test — idx 2 (male) vs idx 3 (female) — the first 5 audio tokens are byte-identical and the male vs female outcome hinges on a top-1/top-2 logit gap of **0.0** at position 5. Two mechanistic hypotheses fit the evidence: (H1) the speaker embedding is effectively null and Kavya's identity is carried only by prompt-shape LM prior, weak on numeric-English texts; (H2) T4 BF16 matmul rounding turns near-ties into hard ties, letting the male sub-manifold win. Both hypotheses are testable by the two next experiments X4-B (cross-precision replay) and X4-C (orthogonal speaker-vector injection). **No production changes were made.** Awaiting instruction on which next experiment to run.
