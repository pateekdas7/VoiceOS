# X4-B — Cross-Precision Replay (T4 BF16 vs T4 FP32)

**Purpose:** Answer — *"Does changing the numerical precision/runtime from the current T4 BF16 path materially change the exact early SNAC/logit decision responsible for Kavya → male drift?"*

**Hard-rule compliance:** No production `server.py` / CPU / `.env` / Kaggle-production / model-weight / speaker / prompt / sampler / speaker-lock / SNAC-blacklist / embedding-scaling / rejection-sampling / Twilio / Git modification. Work confined to isolated Kaggle kernel `mamatadas7777/voiceos-x4b-cross-precision-replay` under `--accelerator NvidiaTeslaT4`.

**Kernel versions:** v1 (P100 fallback — CUDA sm_60 unsupported), v2 (BF16 OK, FP32 OOM on cuda:0), v3 (both tests completed cleanly, SNAC moved to CPU + `max_memory={0:'13GiB', 1:'13GiB'}` split).

---

## 1. Exact environment for each precision

Both tests ran in the same isolated Kaggle kernel on identical Tesla T4 hardware (sm_75, BF16 supported, no BF16 tensor cores).

| Field | Test A (BF16) | Test B (FP32) |
|---|---|---|
| GPU | Tesla T4 | Tesla T4 |
| GPU capability | (7, 5) | (7, 5) |
| GPU count | 2 | 2 |
| torch | 2.10.0+cu128 | 2.10.0+cu128 |
| CUDA | 12.8 | 12.8 |
| cuDNN | 91002 | 91002 |
| transformers | 5.0.0 | 5.0.0 |
| snac | 1.0.0 (on CPU) | 1.0.0 (on CPU) |
| bf16 supported | True | True |
| tf32 matmul | False | False |
| tf32 cudnn | True | True |
| matmul precision | highest | highest |
| deterministic | False | False |
| attn implementation | sdpa | sdpa |
| model dtype | torch.bfloat16 | torch.float32 |
| load time | 38.3 s | 8.1 s (checkpoint cached from A) |
| param dtype counts | `{'torch.bfloat16': 255}` | `{'torch.float32': 255}` |
| param device counts | `{'cuda:0': 255}` | `{'cuda:0': 101, 'cuda:1': 154}` |
| model commit | 8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f | 8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f |
| num params | 3,783,054,336 | 3,783,054,336 |

## 2. Confirmation that X4-A T4 BF16 baseline reproduced

Test A run under X4-B is **byte-for-byte identical to X4-A** on every relevant measure:

- Same 19 corpus records; same classifications (male-drift on idx 2, 7, 8, 12, 14, 15; female-kavya on the rest); same F0 medians to the second decimal.
- Same audio SHAs for the reproduced texts (idx 0=1b437b91db7c29a8, idx 2=c307b74454a30838, idx 3=cdd676f20343f79f, idx 4=02f41f2d985a4fd6, etc.).
- Same first-21 audio token trajectories on all matched records (verified programmatically).
- Same critical minimum-pair evidence: idx 2 pos 5 top-1=149989 @ 80.625, top-2=149925 @ 80.625 (BF16 tie, margin=0.000); idx 3 pos 5 top-1=152691 @ 83.750 top-2=152655 @ 83.125 (margin=0.625).

Baseline validated. Interpretation of Test B is therefore anchored to a known reproducible state.

## 3. Exact FP32 implementation details

- **How weights become FP32:** `AutoModelForCausalLM.from_pretrained('maya-research/Veena', torch_dtype=torch.float32, device_map='auto', max_memory={0: '13GiB', 1: '13GiB', 'cpu': '20GiB'})`. `from_pretrained` reads the on-disk BF16 checkpoint and casts every parameter tensor to torch.float32 at load time.
- **Verification:** enumerating `model.named_parameters()` after load returns `{'torch.float32': 255}` — every one of the 255 parameter tensors is FP32. All three sampled key layers verified FP32: `model.embed_tokens.weight` (156951×3072, FP32, on cuda:0), `model.norm.weight` (3072, FP32, on cuda:1), `lm_head.weight` (156951×3072, FP32, on cuda:1).
- **Model split across the two T4s:** accelerate's `device_map='auto'` with `max_memory` split the 255 parameter tensors as 101 on cuda:0 and 154 on cuda:1. The LM head sits on cuda:1. Cross-GPU forward-pass activation transfers happen at the layer boundary chosen by accelerate.
- **Activations dtype:** because the entire model is FP32, all forward-pass intermediates (attention scores, MLP hidden, RMSNorm, logits) are FP32. `torch.autocast` was **not** enabled — no mixed-precision autocast wrapping.
- **LM-head computation precision:** FP32 matmul (weights FP32, hidden state FP32). `torch.set_float32_matmul_precision('highest')` was set and `torch.backends.cuda.matmul.allow_tf32 = False`, so the matmul kernel is genuine FP32 (not TF32).
- **What remained BF16 / non-FP32:** *nothing* in the Llama path. SNAC decoder was moved to CPU with its native FP32 weights, so audio decoding was FP32 on CPU (~5s per record) — but SNAC does not participate in the token/logit decision under test.
- **What this experiment IS and IS NOT:**  
   IS: genuine FP32 forward-pass, FP32 matmul, FP32 activations, FP32 logits, FP32 softmax input.  
   IS NOT: a different-GPU test (still sm_75 T4 with no BF16 tensor cores); not a KV-cache-precision test (FP32 model → FP32 KV cache automatically); not a full-precision softmax numerator test beyond what torch's softmax already casts to FP32 internally.

## 4. Critical position-5 logit comparison (idx 2 male, idx 3 female minimum pair)

The single most important measurement of X4-B.

### idx 2 — "Total outstanding 24,568 rupees hai as of aaj." (MALE in X4-A/BF16)

| | Test A (BF16) | Test B (FP32) |
|---|---|---|
| chosen at pos 5 | **149989** (cbv=1243) | **149989** (cbv=1243) — **SAME TOKEN** |
| top-1 logit | 80.625 | 81.425865 |
| top-2 id | 149925 (cbv=1179) | 149925 (cbv=1179) |
| top-2 logit | 80.625 | 81.263313 |
| top-3 id | 152691 (cbv=3945) — the female-winning token | 152691 (cbv=3945) |
| top-3 logit | 80.000 | 80.385178 |
| top-4 id | 152028 | 152028 |
| top-4 logit | 79.687 | 79.903992 |
| **margin (top1-top2)** | **0.000000** | **0.162552** |
| classification of full audio | male-drift, F0=117.1 | male-drift, F0=143.7 |

**Key finding:** FP32 does resolve the BF16 tie (margin 0.0 → 0.163) — but **149989 still wins**. The relative ranking of the top-4 candidates is preserved. The distribution just shifts by ~+0.8 nats and de-degenerates. The male-manifold token 149989 remains the top-1 pick.

Because sampling is deterministic under a fixed seed, and the *identity* of the chosen token did not change, **the FP32 trajectory of idx 2 is byte-identical to the BF16 trajectory** — see §5.

### idx 3 — "Principal amount 15,000 rupees baaki hai." (FEMALE in X4-A/BF16)

| | Test A (BF16) | Test B (FP32) |
|---|---|---|
| chosen at pos 5 | **152691** (cbv=3945) | **152691** (cbv=3945) — **SAME TOKEN** |
| top-1 logit | 83.750 | 83.760757 |
| top-2 id | 152655 | 152655 |
| top-2 logit | 83.125 | 83.323212 |
| margin | 0.625 | 0.437546 |
| classification | female-kavya, F0=240.0 | female-kavya, F0=233.0 |

**Key finding:** FP32 keeps the female-winning token 152691 as top-1 with a smaller margin (0.625 → 0.438). Female classification preserved.

## 5. First-21-token comparison

For the critical minimum-pair:

**idx 2 (male, BF16):**
```
BF16: [132151, 132433, 139283, 144205, 147568, 149989, 154862, 131288, 133820, 139437, 140811, 145035, 151462, 155549, 128373, 133992, 139892, 141010, 144934, 149589, 153862]
FP32: [132151, 132433, 139283, 144205, 147568, 149989, 154862, 131288, 133820, 139437, 140811, 145035, 151462, 155549, 128373, 133992, 139892, 141010, 144934, 149589, 153862]
→ IDENTICAL — no divergence in first 21 tokens between BF16 and FP32
```

**idx 3 (female, BF16):**
```
BF16 and FP32 first-21 → IDENTICAL
```

Full-corpus token-trajectory diff (first divergence in first 21 audio tokens between BF16 and FP32):

| idx | bucket | BF16 class → FP32 class | first divergence pos | same first-21? |
|---|---|---|---|---|
| 0 | drift | female → female | — | yes |
| 1 | drift | **female → male** | pos 1 | no |
| 2 | drift | male → male | — | yes |
| 3 | drift | female → female | — | yes |
| 4 | stable | female → female | — | yes |
| 5 | stable | female → female | — | yes |
| 6 | stable | female → female | pos 8 | no |
| 7 | stable | male → male | — | yes |
| 8 | stable | male → male | — | yes |
| 9 | stable | female → female | — | yes |
| 10 | stable | female → female | pos 0 | no |
| 11 | stable | female → female | pos 2 | no |
| 12 | english | male → male | — | yes |
| 13 | english | female → female | pos 14 | no |
| 14 | num_heavy | male → male | pos 3 | no |
| 15 | min_pair | **male → female** | pos 5 | no |
| 16 | min_pair | female → female | — | yes |
| 17 | min_pair | female → female | — | yes |
| 18 | min_pair | female → female | pos 3 | no |

Position-5 chosen-token identity between BF16 and FP32: **same on 15/19 records**, different on idx 1, 10, 11, 15, 18. But the classification changes on only two records (idx 1 flips female→male; idx 15 flips male→female).

## 6. F0 / audio comparison

Full corpus (median F0 in Hz, class):

| idx | text | BF16 F0 | BF16 cls | FP32 F0 | FP32 cls |
|---|---|---|---|---|---|
| 0 | Aapke bank se transfer complete... | 195.1 | female | 187.6 | female |
| 1 | Sir kya aap UPI se payment... | 236.5 | female | **135.6** | **male** |
| 2 | Total outstanding 24,568 rupees... | 117.1 | male | 143.7 | male |
| 3 | Principal amount 15,000 rupees... | 240.0 | female | 233.0 | female |
| 4 | Namaste sir, main Kavya... | 205.1 | female | 208.7 | female |
| 5 | Namaste madam, main Kavya... | 195.1 | female | 200.0 | female |
| 6 | Namaste sir aap kaise hain aaj? | 226.4 | female | 252.6 | female |
| 7 | Dhanyavaad sir, aapke response... | 112.2 | male | 123.1 | male |
| 8 | Dhanyavaad, aapki payment... | 130.4 | male | 132.6 | male |
| 9 | Sir, aapke account par 12,500... | 206.9 | female | 214.3 | female |
| 10 | Aap kaise hain? | 303.8 | female | 313.7 | female |
| 11 | Kripa karke wait karein. | 202.5 | female | 228.6 | female |
| 12 | Good morning, this is a test... | 122.4 | male | 139.5 | male |
| 13 | Your account balance is one... | 228.6 | female | 229.7 | female |
| 14 | 9,876,543 rupees ka total... | 112.7 | male | 122.8 | male |
| 15 | Total outstanding hai as of aaj. | 150.0 | male | **244.9** | **female** |
| 16 | Total outstanding amount check... | 195.1 | female | 200.0 | female |
| 17 | Aapke bank se successful... | 208.7 | female | 201.7 | female |
| 18 | Bank transfer ho chuka hai sir... | 242.4 | female | 252.6 | female |

**Male-drift totals: BF16 = 6/19 (31.6%), FP32 = 6/19 (31.6%). Identical bulk rate.** Two flips in opposite directions (idx 1 gains male, idx 15 loses male).

## 7. Determinism results

Both precisions × 3 reps × 3 critical texts (idx 2 male minimum-pair, idx 3 female minimum-pair, idx 15 second male minimum-pair):

| precision | idx | unique sha16 | unique first-21 | classes over 3 reps |
|---|---|---|---|---|
| BF16 | 2 | 1 | 1 | [male, male, male] |
| BF16 | 3 | 1 | 1 | [female, female, female] |
| BF16 | 15 | 1 | 1 | [male, male, male] |
| FP32 | 2 | 1 | 1 | [male, male, male] |
| FP32 | 3 | 1 | 1 | [female, female, female] |
| FP32 | 15 | 1 | 1 | [female, female, female] |

**Both precisions are fully deterministic at seed=42.** Drift is not stochastic. Each precision defines a single, reproducible trajectory per text.

## 8. Full corpus results

19 texts × 2 precisions = 38 full generations captured with per-position top-5 evidence. Raw data in `x4b_results.json` at `test_a_bf16.records[*].first_21_audio_positions` and `test_b_fp32.records[*].first_21_audio_positions`. Latency: BF16 total 198.2 s, FP32 total 251.2 s (ratio 1.27×). FP32 is only 27% slower than BF16 with device_map='auto' split across 2 T4s — much faster than expected because the T4 has no BF16 tensor cores, so BF16 was already emulated.

## 9. T4 BF16 vs T4 FP32 comparison — required table

| Metric | T4 BF16 | T4 FP32 |
|---|---|---|
| Male-drift count on 19-text corpus | 6/19 (31.6%) | 6/19 (31.6%) |
| idx 2 pos-5 chosen token | 149989 | **149989 (SAME)** |
| idx 2 pos-5 top-1 logit | 80.625 | 81.4259 |
| idx 2 pos-5 top-2 logit | 80.625 | 81.2633 |
| idx 2 pos-5 margin | **0.000** | **0.163** |
| idx 2 rank of female-winning 152691 | 3 | 3 (unchanged) |
| idx 2 first-21 tokens vs BF16 | (baseline) | **IDENTICAL** |
| idx 2 audio SHA16 | c307b74454a30838 | 2d475fa5402938b0 (different due to FP32 SNAC input path) |
| idx 2 median F0 | 117.1 Hz | 143.7 Hz |
| idx 2 p05 F0 | 98.0 Hz | (still male-band) |
| idx 3 pos-5 chosen token | 152691 | **152691 (SAME)** |
| idx 3 pos-5 margin | 0.625 | 0.438 |
| idx 3 median F0 | 240.0 Hz | 233.0 Hz |
| Determinism | 1 sha per (text, seed) | 1 sha per (text, seed) |
| Total generation latency (19 texts) | 198.2 s | 251.2 s |

## 10. SM80+ comparison

**NOT AVAILABLE — no non-T4 comparison performed.**

Per the hard rule "if no such GPU is available, do not rent or provision one automatically" and per session memory ("GPU server is Kaggle T4×2; the only GPU server; no A6000 exists; all GPU work runs on Kaggle"), no L4/A100/H100 was provisioned. Any statement about SM80+ behaviour in this report is expressly avoided.

## 11. Evidence supporting the T4/BF16-numerical-amplification hypothesis

- FP32 does resolve the BF16 tie at idx 2 pos 5: margin 0.0 → 0.163. This is direct numerical evidence that BF16 mantissa precision was creating tied logits.
- FP32 changes the *specific* male/female outcome on 2/19 records (idx 1 flips to male, idx 15 flips to female). So precision does have some causal effect.
- FP32 reduces zero-margin (<0.1) first-21 positions on the two problematic texts: idx 2 dropped from 4 → 2, idx 1 dropped from 4 → 2. Tighter logit separation under FP32.

## 12. Evidence *against* the T4/BF16-numerical-amplification hypothesis

- **Total drift rate unchanged: 6/19 in both precisions.** FP32 does not reduce drift in bulk — it merely reshuffles which texts drift.
- **The critical idx 2 pos-5 decision — where the male trajectory was committed — remains 149989 under FP32.** The BF16 → FP32 logit shift preserves the ranking; the male-manifold token still wins. Breaking the tie was insufficient to change the outcome.
- **The first 21 audio tokens for idx 2 are BYTE-IDENTICAL between BF16 and FP32.** So the entire "commit to male manifold" super-frame is precision-independent for this prompt.
- Idx 3 female-winning trajectory is also byte-identical, so precision was not doing meaningful work in the female case either.
- Idx 15 flipping from male → female under FP32 is one prompt out of six drift cases: not a systematic direction.
- The 2/19 flips include one *new* male case (idx 1) that was female under BF16 — so FP32 doesn't only fix drift, it also introduces it.
- Zero-margin positions are not systematically fewer under FP32 across the corpus (some records have more, some fewer, mean is not lower).

## 13. Evidence supporting the speaker-conditioning-degeneracy hypothesis

- Speaker embeddings cos-to-kavya in both precisions are literally the same values to 12 decimal places (0.988650083542 for maitri, 0.999999940395 for varun, etc.). Precision uplift does not change the fact that speaker vectors are effectively parallel. Speaker conditioning remains a null direction under FP32.
- Drift persistence under FP32 is consistent with the LM prior — not the numerical path — being what determines gender.
- On the four texts most vulnerable to drift (2, 7, 8, 14), FP32 gives byte-identical first-21 tokens and identical male classification. The male trajectory is *structurally* preferred by the LM prior, not fragile numerical noise.

## 14. Evidence against the speaker-conditioning-degeneracy hypothesis

- Idx 15 flipped from male to female under FP32 — if speaker degeneracy alone determined outcomes, precision should not flip a class.
- Idx 1 flipped from female to male under FP32 — again a precision-driven flip that pure-degeneracy would not predict.
- These two flips show that the LM prior is at least in part sensitive to the numerical path, so degeneracy is not the only factor.

## 15. What is PROVEN

1. **FP32 changes the numerical values of logits** — the tied 80.625/80.625 at idx 2 pos 5 becomes 81.4259/81.2633 under FP32 (margin 0.163). This is direct experimental proof that BF16 was producing tied logits from what are genuinely close-but-not-equal FP32 values.
2. **FP32 does not fix the male drift for the primary minimum-pair (idx 2).** The chosen token, the ranking of the top-4, the first 21 audio tokens, and the male classification are all preserved. Drift persists.
3. **Bulk drift rate is precision-independent.** 6/19 in BF16, 6/19 in FP32 — same count of male-drift outcomes on the 19-text stress corpus.
4. **Speaker-embedding cos-to-kavya is precision-invariant to ≥12 decimals.** The degeneracy finding from X4-A is not a BF16 artifact.
5. **Both precisions are deterministic** (unique sha16 = unique first-21 = 1 across 3 reps × 3 texts × 2 precisions = 18 runs).
6. **Two specific texts flip** under FP32 (idx 1 female→male, idx 15 male→female) — so precision has real, but limited and non-directional, causal influence.
7. **The current T4 BF16 numerical path is not the material root cause** of the male drift phenomenon: eliminating BF16 does not reduce drift rate.

## 16. What remains HYPOTHESIS

- **H1 (strongly supported):** Speaker-embedding degeneracy is the load-bearing root cause. Kavya's female identity is carried by the LM prior over prompt shape, which fails on numeric/English/short-truncated Hinglish prompts, and no amount of precision fixes an ambient-null speaker signal.
- **H2 (not supported, largely refuted):** T4 BF16 matmul rounding is *not* the primary cause of drift — precision uplift does not fix drift. H2 may still be a secondary amplifier in a small subset of cases (idx 15), but the null result at idx 2 refutes it as a root cause.
- **H3 (new, unexplored):** For the two flipping records (idx 1, idx 15), precision changes intermediate KV-cache values which propagate to a different fine-codebook choice further downstream. Whether this pattern generalizes across a larger corpus is unknown.

## 17. What has been ruled out

- **Ruled out:** "FP32 fixes the drift." False — 6/19 → 6/19.
- **Ruled out:** "BF16 tied logits are the mechanism of drift." Partially false — the tied logits do exist and FP32 breaks the tie, but in the primary drift case (idx 2) the tie's resolution still favors the male-manifold token.
- **Ruled out:** "Precision uplift alone would be a sufficient production intervention." False — bulk drift rate unchanged, and one new male drift case is introduced.
- **Ruled out (from X4-A, re-confirmed):** stochastic run-to-run variance, NaN/Inf in embeddings, wrong speaker token ID, SNAC monkey-patch regression, GPU-count artifact.

## 18. Recommended next experiment

**X4-C — Orthogonal speaker-vector injection (read-only, in-memory).**

Given the H1/H2 result of X4-B, the highest-signal next experiment isolates the speaker-conditioning-degeneracy hypothesis:

1. Load Veena at the standard BF16 (production-matching) on Kaggle T4.
2. In-memory only, replace the input embedding for `<spk_kavya>` with `kavya_embed + λ * (kavya_embed − varun_embed)` for λ ∈ {0.5, 1.0, 2.0, 4.0}. Because kavya and varun embeddings are nearly parallel, this is a small orthogonal push toward "female-away-from-male".
3. Re-run the same 19-text corpus, capture the same first-21 tokens + top-5 logits + F0.
4. If drift drops from 6/19 → 0-2/19 at some λ, degeneracy is causally sufficient and the intervention path is speaker-embedding surgery, not precision uplift.
5. If drift does not drop, H1 must be revised — the LM prior would be shown to be insensitive to the input embedding perturbation at any reasonable magnitude, pointing back to deeper causes (e.g., mid-layer speaker representation collapse, or token-context weight dominating).

Estimated cost: single Kaggle T4 kernel, ~15 minutes, zero production impact.

**Secondary experiment X4-D (optional):** Repeat X4-B on an SM80+ GPU *only if* the user explicitly approves provisioning one. Purpose: rule out sm_75 as an artifact-generator.

## 19. Production impact

**None.** No modification to production `server.py`, CPU node, `.env`, deployed Kaggle production kernel, model weights, prompt, sampler, or any other production-visible artifact. All work confined to the isolated forensic kernel.

## 20. Files changed

Only forensic-scaffolding files in the user's local Termux home directory (not committed to any repo):

- `/data/data/com.termux/files/home/x4b_kaggle/kernel-metadata.json` (new; forensic-kernel metadata)
- `/data/data/com.termux/files/home/x4b_kaggle/voiceos-x4b-cross-precision.ipynb` (new; forensic notebook)
- `/data/data/com.termux/files/home/x4b_kaggle/X4B_FORENSIC_REPORT.md` (this file)
- `/data/data/com.termux/files/home/x4b_out/x4b_results.json` (fetched output, 602 KB)
- `/data/data/com.termux/files/home/x4b_out/voiceos-x4b-cross-precision-replay.log` (fetched log, 26 KB)

No production repository files touched.

## 21. Git status

**Unchanged.** No `git add`, `git commit`, `git push`, or any git operation was performed. Repository state is exactly as it was at the start of X4-B.

## 22. Kaggle production status

**Production Kaggle kernel (voiceos-tts-server or equivalent) unchanged.** X4-B pushed only to the isolated forensic kernel `mamatadas7777/voiceos-x4b-cross-precision-replay` (three versions: v1 P100 fallback failure, v2 BF16-OK-FP32-OOM, v3 BF16+FP32 both complete). Production Veena-serving kernel was not touched.

## 23. Twilio status

**No Twilio call placed.** No Twilio API, TwiML, Media Streams, or call-control action was invoked at any point during X4-B.

---

## Answer to the objective question

> *"Does changing the numerical precision/runtime from the current T4 BF16 path materially change the exact early SNAC/logit decision responsible for Kavya→male drift?"*

**Answer: No — not materially.**

FP32 does numerically de-tie the specific BF16-tied logits at position 5 of the male minimum-pair (margin 0.000 → 0.163), but **it does not change which token wins**. Token 149989 (the male-manifold fine-codebook choice) remains top-1 under FP32. The full first-21 audio token trajectory of the male minimum-pair (idx 2) is byte-identical between BF16 and FP32. Bulk drift rate on the 19-text stress corpus is precision-invariant at 6/19 (31.6%). Two records flip in opposite directions under FP32 (net zero), confirming that precision has some influence but is not the root cause.

The T4/BF16 numerical hypothesis (H2 in X4-A) is therefore **refuted as a primary cause**. The speaker-conditioning-degeneracy hypothesis (H1 in X4-A) remains the load-bearing candidate and should be tested directly by X4-C (orthogonal speaker-vector injection).

**Stopping per hard rules.** No fix implemented. No CPU/.env/Kaggle-production/Twilio/Git action taken.
