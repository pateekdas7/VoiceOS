"""Four-approach forensic corpus.

Each entry is (approach, subcategory, variant_key, text, notes).

BASELINE       — original Hinglish forensic corpus (19 items). Reused so this run
                 produces its own baseline (do not compare across runs of Veena;
                 seed stochasticity may still shift F0 slightly).

DEVANAGARI     — semantically equivalent full/partial Devanagari versions of the
                 baseline items that are relevant to drift/spelling/acronym
                 hypotheses. Two flavours per text:
                   full  = maximally native Devanagari
                   mixed = same as production observed (Devanagari + English nouns)

SPELLING       — same semantic content, controlled spelling variation of a single
                 lexical target (dhanyavaad, shukriya, kripya, namaste, intezaar).

ACRONYM        — same sentence, three renderings of one acronym:
                   raw     = "UPI"
                   spelled = "yoo pee aaye"
                   deva    = "यू पी आई"

PROMPT         — five focused texts (2 drift + 3 stable) rendered under four
                 speaker-prompt reinforcement variants:
                   V1 = "<spk_kavya> {text}"          (baseline)
                   V2 = "<spk_kavya> <spk_kavya> {text}"
                   V3 = "<spk_kavya> {text} <spk_kavya>"
                   V4 = "<spk_kavya>{text}"           (no space)

All texts preserve semantic meaning as closely as possible. Notes column records
any unavoidable semantic shift or transliteration choice.
"""
from typing import List, Tuple

Entry = Tuple[str, str, str, str, str]

# ---------------------------------------------------------------------------
# 1. BASELINE (19 items — identical to part_c_a6000/l4_forensic.py CORPUS)
# ---------------------------------------------------------------------------
BASELINE: List[Entry] = [
    ("BASELINE", "drift",     "B00", "Aapke bank se transfer complete ho gaya hai.",             ""),
    ("BASELINE", "drift",     "B01", "Sir kya aap UPI se payment karna prefer karenge?",         ""),
    ("BASELINE", "drift",     "B02", "Total outstanding 24,568 rupees hai as of aaj.",           ""),
    ("BASELINE", "drift",     "B03", "Principal amount 15,000 rupees baaki hai.",                ""),
    ("BASELINE", "stable",    "B04", "Namaste sir, main Kavya bol rahi hoon Rajat Finance se.",  ""),
    ("BASELINE", "stable",    "B05", "Namaste madam, main Kavya bol rahi hoon.",                 ""),
    ("BASELINE", "stable",    "B06", "Namaste sir aap kaise hain aaj?",                          ""),
    ("BASELINE", "stable",    "B07", "Dhanyavaad sir, aapke response ka intezaar rahega.",       ""),
    ("BASELINE", "stable",    "B08", "Dhanyavaad, aapki payment successful ho gayi hai.",        ""),
    ("BASELINE", "stable",    "B09", "Sir, aapke account par 12,500 rupees ka outstanding hai.", ""),
    ("BASELINE", "stable",    "B10", "Aap kaise hain?",                                          ""),
    ("BASELINE", "stable",    "B11", "Kripa karke wait karein.",                                 ""),
    ("BASELINE", "english",   "B12", "Good morning, this is a test message.",                    ""),
    ("BASELINE", "english",   "B13", "Your account balance is one thousand five hundred rupees.",""),
    ("BASELINE", "num_heavy", "B14", "9,876,543 rupees ka total amount pending hai.",            ""),
    ("BASELINE", "min_pair",  "B15", "Total outstanding hai as of aaj.",                         ""),
    ("BASELINE", "min_pair",  "B16", "Total outstanding amount check kar rahi hoon.",            ""),
    ("BASELINE", "min_pair",  "B17", "Aapke bank se successful transaction huwa hai.",           ""),
    ("BASELINE", "min_pair",  "B18", "Bank transfer ho chuka hai sir, dhyaan dijiye.",           ""),
]

# ---------------------------------------------------------------------------
# 2. DEVANAGARI (semantically equivalent, paired to a BASELINE item by suffix)
#    variant_key = "D00_full", "D00_mixed", etc., where "00" maps to BASELINE B00
# ---------------------------------------------------------------------------
DEVANAGARI: List[Entry] = [
    # B00 drift — bank transfer complete
    ("DEVANAGARI", "drift", "D00_mixed", "आपके bank से transfer complete हो गया है।",
     "matches production mixed-script style"),
    ("DEVANAGARI", "drift", "D00_full",  "आपके बैंक से हस्तांतरण पूर्ण हो गया है।",
     "hastantaran = formal transfer; less common in speech"),

    # B01 drift — UPI payment
    ("DEVANAGARI", "drift", "D01_mixed", "सर क्या आप UPI से payment करना prefer करेंगे?",
     "UPI kept as acronym for direct comparison to B01"),
    ("DEVANAGARI", "drift", "D01_full",  "सर क्या आप यू पी आई से भुगतान करना पसंद करेंगे?",
     "acronym spelled in Devanagari + bhugtan = payment"),

    # B02 drift — outstanding 24,568
    ("DEVANAGARI", "drift", "D02_mixed", "कुल outstanding 24,568 रुपये है as of आज।",
     ""),
    ("DEVANAGARI", "drift", "D02_full",  "कुल बकाया चौबीस हज़ार पाँच सौ अड़सठ रुपये है आज तक।",
     "number spelled out in Hindi — biggest semantic-shift risk"),

    # B03 drift — principal 15,000
    ("DEVANAGARI", "drift", "D03_mixed", "Principal amount 15,000 रुपये बाकी है।",
     ""),
    ("DEVANAGARI", "drift", "D03_full",  "मूल राशि पंद्रह हज़ार रुपये बाकी है।",
     ""),

    # B04 stable — Namaste sir Kavya
    ("DEVANAGARI", "stable", "D04_mixed", "नमस्ते सर, मैं Kavya बोल रही हूँ Rajat Finance से।",
     ""),
    ("DEVANAGARI", "stable", "D04_full",  "नमस्ते सर, मैं काव्या बोल रही हूँ रजत फ़ाइनेंस से।",
     "Kavya→काव्या; Finance→फ़ाइनेंस"),

    # B07 stable — Dhanyavaad + intezaar
    ("DEVANAGARI", "stable", "D07_mixed", "धन्यवाद सर, आपके response का इंतज़ार रहेगा।",
     ""),
    ("DEVANAGARI", "stable", "D07_full",  "धन्यवाद सर, आपके उत्तर का इंतज़ार रहेगा।",
     "response→uttar"),

    # B08 stable — Dhanyavaad payment successful
    ("DEVANAGARI", "stable", "D08_mixed", "धन्यवाद, आपकी payment successful हो गयी है।",
     ""),
    ("DEVANAGARI", "stable", "D08_full",  "धन्यवाद, आपका भुगतान सफल हो गया है।",
     ""),

    # B09 stable — account outstanding 12,500
    ("DEVANAGARI", "stable", "D09_mixed", "सर, आपके account पर 12,500 रुपये का outstanding है।",
     ""),
    ("DEVANAGARI", "stable", "D09_full",  "सर, आपके खाते पर बारह हज़ार पाँच सौ रुपये का बकाया है।",
     ""),

    # B11 stable — Kripa karke wait karein
    ("DEVANAGARI", "stable", "D11_mixed", "कृपया wait करें।",
     ""),
    ("DEVANAGARI", "stable", "D11_full",  "कृपया प्रतीक्षा करें।",
     ""),

    # B14 num_heavy — 9,876,543
    ("DEVANAGARI", "num_heavy", "D14_mixed", "9,876,543 रुपये का total amount pending है।",
     ""),
    ("DEVANAGARI", "num_heavy", "D14_full",  "अठानवे लाख छिहत्तर हज़ार पाँच सौ तैंतालीस रुपये कुल बकाया है।",
     "number spelled — expected phonetic shift"),
]

# ---------------------------------------------------------------------------
# 3. SPELLING variants — same sentence, single-lexeme spelling changed
# ---------------------------------------------------------------------------
SPELLING: List[Entry] = [
    # dhanyavaad family (baseline B07: "Dhanyavaad sir, aapke response ka intezaar rahega.")
    ("SPELLING", "dhanyavaad", "S_DV_01", "Dhanyavaad sir, aapke response ka intezaar rahega.",
     "baseline = 'Dhanyavaad'"),
    ("SPELLING", "dhanyavaad", "S_DV_02", "Dhanyawad sir, aapke response ka intezaar rahega.",
     "'Dhanyawad' (w vs v)"),
    ("SPELLING", "dhanyavaad", "S_DV_03", "Dhanya waad sir, aapke response ka intezaar rahega.",
     "'Dhanya waad' (split, fragmented)"),
    ("SPELLING", "dhanyavaad", "S_DV_04", "Dhanyabaad sir, aapke response ka intezaar rahega.",
     "'Dhanyabaad' (Bengali-style spelling)"),
    ("SPELLING", "dhanyavaad", "S_DV_05", "Shukriya sir, aapke response ka intezaar rahega.",
     "Shukriya (Urdu-origin synonym)"),
    ("SPELLING", "dhanyavaad", "S_DV_06", "Shukriyaa sir, aapke response ka intezaar rahega.",
     "Shukriyaa (double-a)"),

    # namaste family (baseline B06: "Namaste sir aap kaise hain aaj?")
    ("SPELLING", "namaste", "S_NM_01", "Namaste sir aap kaise hain aaj?",
     "baseline"),
    ("SPELLING", "namaste", "S_NM_02", "Namaskaar sir aap kaise hain aaj?",
     "Namaskaar (more formal)"),
    ("SPELLING", "namaste", "S_NM_03", "Namaskar sir aap kaise hain aaj?",
     "Namaskar (short-a)"),
    ("SPELLING", "namaste", "S_NM_04", "Namastey sir aap kaise hain aaj?",
     "Namastey (English-friendly)"),

    # kripya family (baseline B11: "Kripa karke wait karein.")
    ("SPELLING", "kripya", "S_KP_01", "Kripa karke wait karein.",
     "baseline = 'Kripa karke'"),
    ("SPELLING", "kripya", "S_KP_02", "Kripya wait karein.",
     "Kripya (single word, more formal)"),
    ("SPELLING", "kripya", "S_KP_03", "Krupya wait karein.",
     "Krupya (regional variant)"),
]

# ---------------------------------------------------------------------------
# 4. ACRONYM variants — three renderings of the same acronym in the same host
# ---------------------------------------------------------------------------
ACRONYM: List[Entry] = [
    # UPI in the known-drift sentence (baseline B01)
    ("ACRONYM", "UPI", "A_UPI_raw",     "Sir kya aap UPI se payment karna prefer karenge?",
     "baseline"),
    ("ACRONYM", "UPI", "A_UPI_spelled", "Sir kya aap yoo pee aaye se payment karna prefer karenge?",
     "letters written phonetically"),
    ("ACRONYM", "UPI", "A_UPI_deva",    "Sir kya aap यू पी आई se payment karna prefer karenge?",
     "acronym in Devanagari"),

    # OTP
    ("ACRONYM", "OTP", "A_OTP_raw",     "Sir aapko OTP mila hai kya?",
     ""),
    ("ACRONYM", "OTP", "A_OTP_spelled", "Sir aapko oh tee pee mila hai kya?",
     ""),
    ("ACRONYM", "OTP", "A_OTP_deva",    "Sir aapko ओ टी पी mila hai kya?",
     ""),

    # SMS
    ("ACRONYM", "SMS", "A_SMS_raw",     "Aapko SMS bhej diya hai confirmation ke liye.",
     ""),
    ("ACRONYM", "SMS", "A_SMS_spelled", "Aapko es em es bhej diya hai confirmation ke liye.",
     ""),
    ("ACRONYM", "SMS", "A_SMS_deva",    "Aapko एस एम एस bhej diya hai confirmation ke liye.",
     ""),

    # EMI
    ("ACRONYM", "EMI", "A_EMI_raw",     "Aapki EMI 5,000 rupees hai monthly.",
     ""),
    ("ACRONYM", "EMI", "A_EMI_spelled", "Aapki ee em aaye 5,000 rupees hai monthly.",
     ""),
    ("ACRONYM", "EMI", "A_EMI_deva",    "Aapki ई एम आई 5,000 rupees hai monthly.",
     ""),
]

# ---------------------------------------------------------------------------
# 5. PROMPT reinforcement — 5 texts × 4 wrapper variants = 20 items
#    Wrapper is applied at generation time via `wrapper_key`; the `text` here
#    is only the payload sentence. Wrapper templates live in the harness.
# ---------------------------------------------------------------------------
PROMPT_PAYLOADS: List[Tuple[str, str, str]] = [
    # (subcategory, key, text)
    ("drift",  "P_UPI",       "Sir kya aap UPI se payment karna prefer karenge?"),        # B01
    ("drift",  "P_OUT24k",    "Total outstanding 24,568 rupees hai as of aaj."),          # B02
    ("stable", "P_NAM",       "Namaste sir aap kaise hain aaj?"),                          # B06
    ("stable", "P_DV",        "Dhanyavaad sir, aapke response ka intezaar rahega."),       # B07
    ("stable", "P_KRIPA",     "Kripa karke wait karein."),                                 # B11
]

PROMPT_WRAPPERS = [
    ("V1", "<spk_kavya> {text}"),
    ("V2", "<spk_kavya> <spk_kavya> {text}"),
    ("V3", "<spk_kavya> {text} <spk_kavya>"),
    ("V4", "<spk_kavya>{text}"),
]

# Expanded to Entry-list for the harness
PROMPT: List[Entry] = []
for sub, pkey, payload in PROMPT_PAYLOADS:
    for vkey, wrapper in PROMPT_WRAPPERS:
        variant_key = f"{pkey}_{vkey}"
        PROMPT.append(("PROMPT", sub, variant_key, wrapper.format(text=payload),
                       f"payload={pkey} wrapper={vkey}"))


ALL_CORPUS: List[Entry] = BASELINE + DEVANAGARI + SPELLING + ACRONYM + PROMPT

# For prompt-reinforcement rows the harness must know NOT to wrap them again in
# `<spk_kavya>` prefix. We flag those by approach == "PROMPT".
# Everything else is wrapped by the harness with the default V1 prefix.
