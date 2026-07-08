# VoiceOS v2 — Documentation Suite

## Document 7 — Voice Style Guide

**Type:** Canonical TTS speaking standard (documentation layer over Volumes 1–7)
**Status:** Living reference · **Owner:** Voice Runtime / AI Engineering
**Authority:** Volumes 1–7 are immutable + canonical. This guide **consolidates** the speaking standard already defined in V1 Ch15–20 (speech rendering, voice style, prosody, emotion, TTS streaming) into the canonical reference every TTS model + voice profile must follow. It does not change synthesis behavior. Citations: `V<n> Ch<c>`.

> **Scope.** This is the *how it is spoken* standard — pronunciation, normalization, prosody, pauses, emphasis, rate, emotion mapping, and forbidden pronunciations — for Hindi / Hinglish / English on Veena TTS (V1 Ch17). It does **not** govern *what* is said (that is the Prompt Library, DocSuite-06, under Output Validation, AR-6). Voice profiles + emotion are configured via V5 Ch14; tone settings are clamped (V1 Ch16).

---

## 1. Voice principles

- **Clarity over flourish.** Financial information (amounts, dates, references) must be unambiguous and correctly pronounced — comprehension is a compliance + outcome concern.
- **Warm, respectful, non-threatening.** The voice embodies RBI fair-practice tone (V4 Ch2): calm, patient, never harsh or pressuring — consistent regardless of content.
- **Natural code-switching.** Hindi/Hinglish/English blend as a natural Indian speaker would; switch smoothly mid-sentence without jarring shifts.
- **Consistency.** The same term is pronounced the same way every time (see terminology tables) — predictability builds trust + comprehension.

## 2. Language handling

### 2.1 Hindi
- Render Devanagari naturally; respect inherent-vowel (schwa) deletion rules (e.g., "नमस्ते" → *namaste*, not *namastey*).
- Retroflex/dental distinctions preserved; nasalization (anusvara/chandrabindu) rendered.
- Honorifics ("ji", "aap") spoken with appropriate warmth.

### 2.2 Hinglish (code-mixed)
- The default conversational register. English nouns (loan, payment, EMI, account) embedded in Hindi grammar, pronounced with natural Indian-English phonology — not anglicized to the point of unfamiliarity, not over-Hindi-ized.
- Numbers + amounts: spoken in the register the customer will best understand (commonly English digits within Hindi sentences) — see §4.

### 2.3 English
- Indian English pronunciation standard (not American/British affectation). Clear, neutral, professional.

### 2.4 Devanagari normalization (V1 Ch15)
- Text normalization expands/normalizes before synthesis: digits → words where appropriate, abbreviations expanded, dates/amounts to spoken form (§4), Devanagari + Latin scripts handled in one utterance.
- Normalization is deterministic + part of speech rendering (V1 Ch15); it never alters meaning (Output Validation already passed).

## 3. Terminology pronunciation (canonical)

> The canonical pronunciation for domain terms. **Consistency is mandatory** — these never vary by model or profile.

### 3.1 Finance / loan / collections terms
| Term | Canonical pronunciation | Notes |
|---|---|---|
| EMI | "ee-em-eye" (letters) | never "emmy" |
| loan | "lone" | Indian-English |
| account | "a-kount" | clear final consonant |
| installment | "in-staal-ment" | — |
| outstanding | "out-standing" | clear |
| due date | "dyoo date" | — |
| settlement | "settle-ment" | — |
| overdue | "over-dyoo" | — |
| principal | "prin-si-pal" | not "principle" |
| interest | "in-trest" | — |
| penalty | "pe-nal-tee" | — |
| NEFT / UPI / IMPS | letter-by-letter | payment rails |

### 3.2 Brand / organization names
- `{org_name}` and brand names are pronounced per a **per-tenant pronunciation override** (configured in the voice profile, V5 Ch14) — brand names must be said correctly; when unknown, fall back to careful phonetic rendering. Maintain a per-tenant brand-pronunciation list.

### 3.3 Numbers, amounts, dates (§4 details)
Rendered to natural spoken form by normalization (V1 Ch15) — see §4.

## 4. Numbers, amounts & dates

- **Amounts (Indian numbering):** use lakh/crore conventions where natural; e.g., ₹1,50,000 → "ek lakh pachas hazaar rupaye" (Hindi/Hinglish) or "one lakh fifty thousand rupees" (English). Always include the currency word ("rupaye"/"rupees"). **Never** drop or round an amount — the figure comes from facts (Law of Authority) and must be spoken exactly.
- **Dates:** spoken naturally in the call language; e.g., 2026-07-15 → "pandrah July" / "fifteenth July". Avoid ambiguous numeric date forms.
- **Loan references / account numbers:** spoken digit-by-digit (or grouped) clearly for confirmation; never slurred.
- **Determinism:** amount/date rendering is deterministic + must match the source figure exactly (verified against the `ResponsePlan` fact).

## 5. Prosody

### 5.1 Pause rules
- **Clause boundaries:** brief pause (natural breath) — supports the streaming-clause overlap (V1 Ch23) without sounding clipped.
- **Before/after key facts:** a slight pause around amounts + dates aids comprehension (e.g., "aapka outstanding … {amount} … hai").
- **After questions:** pause to yield the turn (supports endpointing + barge-in, V1 Ch6/21).
- **No dead air:** pauses are natural micro-pauses, never gaps that read as a dropped call (the playback scheduler guards coherence, RI-6).

### 5.2 Emphasis
- Light emphasis on key facts (amount, due date, the ask) for clarity — never aggressive stress that reads as pressure.
- Emphasis markers come from voice prompts (DocSuite-06 §11) → prosody (V1 Ch18); clamped to natural ranges (V1 Ch16).

### 5.3 Speaking rate
- **Default:** measured, comprehensible — slightly slower than casual for financial clarity, especially around numbers.
- **Adaptive:** may slow for amounts/dates/verification; never rushed. Rate is within clamped bounds (V1 Ch16).

### 5.4 Intonation
- Warm, falling intonation for statements (reassuring); gentle rising for questions; never harsh or clipped.

## 6. Emotion mapping (V1 Ch19 / V2 Ch13)

Emotion state (V2 Ch13) maps to prosody/voice-quality — **empathy never overrides policy/risk** (Four-Class Hierarchy, V2 Ch1); emotion shapes *delivery*, not *content*.

| Emotion state | Voice rendering |
|---|---|
| Neutral/cooperative | warm, efficient, default rate |
| Distressed | gentle, slower, softer, patient, reassuring |
| Frustrated | calm, steady, validating; slightly slower; no defensiveness |
| Confused | clearer, slower, simpler phrasing, more pauses |
| Hesitant | encouraging, unhurried, supportive |

## 7. Voice quality

- **Target MOS** (Mean Opinion Score) per V1 Ch17 / DocSuite-10 — natural, intelligible, consistent.
- **No artifacts:** no glitches/clipping/robotic prosody (monitored, V7 Ch7; failures → fallback voice, V3 Ch13).
- **Consistent persona:** one coherent voice identity per profile across a call (and across calls for a tenant) — set by the voice profile (V5 Ch14).
- **24 kHz** synthesis (Veena, V1 Ch17), streamed (SOXR resampling as needed, V1 Ch20).

## 8. Forbidden pronunciations & renderings

> Hard rules — these are **never** acceptable and are caught in pronunciation testing (DocSuite-08/10).

- **Never** mispronounce an amount, date, or loan reference — financial facts must be exact + clear.
- **Never** anglicize Hindi names/honorifics into unrecognizability; **never** over-Hindi-ize common English finance terms into confusion.
- **Never** use harsh, clipped, sarcastic, or pressuring intonation — incompatible with fair-practice tone (V4 Ch2).
- **Never** read raw unnormalized tokens (e.g., "Rs.150000" verbatim, ISO date strings, codes) — normalization (V1 Ch15) must convert to natural speech first.
- **Never** insert dead air that reads as a dropped call (RI-6 coherence).
- **Never** mispronounce the tenant's brand/org name — use the per-tenant override (§3.2).
- **Never** let emotion rendering override or contradict the validated content (emotion = delivery only).

## 9. Examples (canonical renderings)

| Text (post-validation) | Canonical spoken rendering |
|---|---|
| "Outstanding ₹1,50,000 due 2026-07-15" | "aapka outstanding … ek lakh pachas hazaar rupaye … pandrah July ko due hai" |
| "EMI of ₹5,000" | "paanch hazaar rupaye ki EMI" ("ee-em-eye") |
| "Verify date of birth" | "verification ke liye, apni date of birth confirm kijiye" (warm, slight pause before the ask) |
| "Connect to a human agent" | "main aapko ek senior agent se connect karta hoon" (reassuring, falling intonation) |

## 10. Relationship to other documents
- **What is said:** Prompt Library (DocSuite-06), under Output Validation (AR-6).
- **How it is spoken:** this guide (DocSuite-07) → prosody/emotion/normalization (V1 Ch15–20).
- **Voice/emotion config:** AI Config Platform (V5 Ch14) — voice profiles, emotion profiles, per-tenant brand pronunciations.
- **Evaluation:** pronunciation + MOS + prosody naturalness (DocSuite-10 / V1 Ch17).
- **Monitoring/fallback:** voice-quality monitoring (V7 Ch7); degraded TTS → fallback voice (V3 Ch13).

---

## Version history & change log

| Version | Date | Change | Owner |
|---|---|---|---|
| 1.0 | (initial) | Voice Style Guide consolidated from V1 Ch15–20 | Voice Runtime / Documentation |

**Change-log policy:** any change to pronunciation/prosody/emotion standards in V1 (the architecture) MUST be reflected here; this guide is the canonical cross-model reference but never overrides V1. The suite consistency audit (DocSuite-12) verifies every voice rule has documentation.

*End of Document 7 — Voice Style Guide.*
