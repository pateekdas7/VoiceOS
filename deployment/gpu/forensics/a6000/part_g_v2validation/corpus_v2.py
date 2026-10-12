"""V2 validation corpus — 130 production-realistic utterances.

Categories (labelled explicitly, some overlap by design):
  KD  known-drift baseline (from prior forensics)
  DV  dhanyavaad variants in multiple hosts
  PAY payment / outstanding / bank
  NUM number-heavy (amounts, dates, phone numbers)
  ACR UPI/OTP/SMS/EMI variants (raw form)
  EN  English-only
  HG  Romanized Hinglish
  DV_ pure Devanagari
  MIX mixed Devanagari + English (production traffic style — HIGH PRIORITY)
  SH  short (< 6 words)
  LG  long (> 15 words)
  ST  stable female controls (from prior)
  NM  Indian names in financial context
  DT  dates
  FIN financial acronyms (KYC/PAN/Aadhaar)
  MP  minimal pairs

For every text we also emit UPI_normalized variant if it contains the literal
substring "UPI" (rewriting to "yoo pee aaye" in the sentence). And every
number-heavy or mixed-script text is a candidate for the V2+Deva diagnostic.
"""
from typing import List, Tuple

Row = Tuple[str, str, str]  # (category, key, text)

CORPUS: List[Row] = [
    # ---- Known-drift baseline (from prior forensic) ----
    ("KD",  "kd_bank_transfer",   "Aapke bank se transfer complete ho gaya hai."),
    ("KD",  "kd_upi_pay",         "Sir kya aap UPI se payment karna prefer karenge?"),
    ("KD",  "kd_outstanding_24k", "Total outstanding 24,568 rupees hai as of aaj."),
    ("KD",  "kd_principal_15k",   "Principal amount 15,000 rupees baaki hai."),
    ("KD",  "kd_dhanyavaad_pay",  "Dhanyavaad, aapki payment successful ho gayi hai."),
    ("KD",  "kd_num_9m",          "9,876,543 rupees ka total amount pending hai."),
    ("KD",  "kd_min1",            "Total outstanding hai as of aaj."),

    # ---- Dhanyavaad in multiple contexts ----
    ("DV",  "dv_intezaar",        "Dhanyavaad sir, aapke response ka intezaar rahega."),
    ("DV",  "dv_time",            "Dhanyavaad aapke time ke liye."),
    ("DV",  "dv_short",           "Dhanyavaad sir."),
    ("DV",  "dv_cooperation",     "Dhanyavaad aapke cooperation ke liye."),
    ("DV",  "dv_payment_kal",     "Dhanyavaad, aapka payment kal receive ho jayega."),
    ("DV",  "dv_confirmation",    "Dhanyavaad, aapki confirmation mil gayi hai."),

    # ---- Payment / outstanding / bank host sentences ----
    ("PAY", "pay_complete_sir",   "Sir, aapka payment complete ho gaya hai."),
    ("PAY", "pay_pending_madam",  "Madam, aapka payment pending hai."),
    ("PAY", "pay_hdfc",           "HDFC Bank se transfer ho chuka hai."),
    ("PAY", "pay_outstanding_1l", "Aapka outstanding 1,00,000 rupees hai."),
    ("PAY", "pay_balance_check",  "Sir, aap apna balance check kar lijiye."),
    ("PAY", "pay_refund",         "Refund process ho chuka hai sir."),
    ("PAY", "pay_neft_success",   "NEFT transfer successful ho gaya hai."),

    # ---- Number-heavy ----
    ("NUM", "num_amount_50k",     "Aapka amount 50,000 rupees hai monthly."),
    ("NUM", "num_amount_87k",     "Total 87,650 rupees ka outstanding baaki hai."),
    ("NUM", "num_seven_digit",    "1,25,00,000 rupees ka loan amount finalize ho gaya."),
    ("NUM", "num_phone",          "Aapka phone number 9876543210 register hai."),
    ("NUM", "num_pan",            "PAN number ABCDE1234F verify ho gaya hai."),
    ("NUM", "num_percentage",     "Interest rate 8.5 percent hai monthly."),
    ("NUM", "num_installments",   "36 installments mein loan repay hoga."),

    # ---- Acronym raw ----
    ("ACR", "acr_upi_1",          "Sir, UPI ka use karke payment karein."),
    ("ACR", "acr_upi_2",          "Aap UPI ke through paise bhej sakte hain."),
    ("ACR", "acr_upi_3",          "UPI transaction fail ho gaya kya sir?"),
    ("ACR", "acr_upi_4",          "Sir, UPI PIN sahi hai na?"),
    ("ACR", "acr_upi_5",          "Aapka UPI ID kya hai?"),
    ("ACR", "acr_otp_1",          "Sir, aapko OTP mila hai kya?"),
    ("ACR", "acr_otp_3",          "Aapka OTP 6 digit ka hoga."),
    ("ACR", "acr_sms_1",          "Aapko SMS bhej diya hai confirmation ke liye."),
    ("ACR", "acr_sms_3",          "SMS delivered ho gaya hai aapke number pe."),
    ("ACR", "acr_emi_1",          "Aapki EMI 5,000 rupees hai monthly."),
    ("ACR", "acr_emi_2",          "EMI due date 5 tareek hai har mahine."),

    # ---- English-only ----
    ("EN",  "en_good_morning",    "Good morning, this is a test message."),
    ("EN",  "en_balance",         "Your account balance is one thousand five hundred rupees."),
    ("EN",  "en_thanks",          "Thank you for your time today."),
    ("EN",  "en_confirm_pay",     "Please confirm the payment details."),
    ("EN",  "en_call_back",       "I will call you back later this afternoon."),
    ("EN",  "en_details_shared",  "The transaction details have been shared with you."),

    # ---- Romanized Hinglish (stable domain) ----
    ("HG",  "hg_kaise_hain",      "Aap kaise hain?"),
    ("HG",  "hg_namaste_sir",     "Namaste sir aap kaise hain aaj?"),
    ("HG",  "hg_kripa_wait",      "Kripa karke wait karein."),
    ("HG",  "hg_thoda_time",      "Thoda time dijiye sir main check karke bataati hoon."),
    ("HG",  "hg_maaf_kariye",     "Maaf kariye sir, thoda technical issue tha."),

    # ---- Pure Devanagari ----
    ("DVN", "dvn_namaste",        "नमस्ते सर, आप कैसे हैं आज?"),
    ("DVN", "dvn_dhanyavaad",     "धन्यवाद सर, आपके उत्तर का इंतज़ार रहेगा।"),
    ("DVN", "dvn_payment",        "आपका भुगतान सफल हो गया है।"),
    ("DVN", "dvn_bakaya",         "आपके खाते पर बारह हज़ार पाँच सौ रुपये का बकाया है।"),
    ("DVN", "dvn_kripya",         "कृपया प्रतीक्षा करें।"),
    ("DVN", "dvn_outstanding_24", "कुल बकाया चौबीस हज़ार पाँच सौ अड़सठ रुपये है आज तक।"),
    ("DVN", "dvn_upi",            "सर क्या आप यू पी आई से भुगतान करना पसंद करेंगे?"),
    ("DVN", "dvn_9m",             "अठानवे लाख छिहत्तर हज़ार पाँच सौ तैंतालीस रुपये कुल बकाया है।"),
    ("DVN", "dvn_kavya",          "मैं काव्या बोल रही हूँ रजत फ़ाइनेंस से।"),
    ("DVN", "dvn_meeting_time",   "बैठक कल सुबह दस बजे होगी।"),
    ("DVN", "dvn_maaf",           "माफ़ कीजिए सर, थोड़ी तकनीकी समस्या थी।"),
    ("DVN", "dvn_shukriya",       "शुक्रिया सर आपके समय के लिए।"),

    # ---- Mixed Devanagari + English (PRODUCTION STYLE — CRITICAL) ----
    ("MIX", "mix_karnataka",      "कर्नाटक बैंक से आपका payment complete हो गया है।"),
    ("MIX", "mix_hdfc_credit",    "HDFC Bank से 5,000 रुपये credit हो गए हैं।"),
    ("MIX", "mix_icici_stmt",     "ICICI Bank का statement भेज दिया है।"),
    ("MIX", "mix_upi_success",    "आपका UPI payment successful रहा।"),
    ("MIX", "mix_otp_shared",     "आपने OTP share किया कया?"),
    ("MIX", "mix_pending_amount", "Pending amount 25,000 रुपये है।"),
    ("MIX", "mix_pan_verify",     "आपका PAN card verify हो गया है।"),
    ("MIX", "mix_aadhaar_link",   "Aadhaar link करने की process complete है।"),
    ("MIX", "mix_kyc_pending",    "आपकी KYC verification pending है।"),
    ("MIX", "mix_transfer_neft",  "NEFT transfer से 50,000 रुपये आ गए हैं।"),
    ("MIX", "mix_customer_care",  "Customer care से contact करें आप।"),
    ("MIX", "mix_dhanyavaad_pay", "धन्यवाद, आपका payment successful रहा है।"),
    ("MIX", "mix_axis_bank_bal",  "Axis Bank का balance 12,500 रुपये है।"),
    ("MIX", "mix_sms_link",       "SMS में link आया होगा, click कर लीजिए।"),
    ("MIX", "mix_emi_5k",         "आपकी EMI 5,000 रुपये monthly है।"),

    # ---- Short (< 6 words) ----
    ("SH",  "sh_thanks",          "Dhanyavaad sir."),
    ("SH",  "sh_namaste",         "Namaste."),
    ("SH",  "sh_ok",              "Theek hai sir."),

    # ---- Long (> 15 words) ----
    ("LG",  "lg_full_call",       "Namaste sir, main Kavya bol rahi hoon Rajat Finance se, aapke loan account ke regarding call kar rahi hoon aur payment status update karna chahti hoon."),
    ("LG",  "lg_confirmation",    "Sir, aapki total outstanding 45,678 rupees hai jismein principal 40,000 rupees aur interest 5,678 rupees included hai monthly EMI ke liye."),
    ("LG",  "lg_mix_long",        "आपके HDFC Bank account से 25,000 रुपये का debit हुआ है aur transaction ID SBIN0001234 aap SMS में check kar sakte hain confirmation ke liye."),

    # ---- Stable female controls ----
    ("ST",  "st_namaste_madam",   "Namaste madam, main Kavya bol rahi hoon."),
    ("ST",  "st_namaste_kavya",   "Namaste sir, main Kavya bol rahi hoon Rajat Finance se."),
    ("ST",  "st_bank_success",    "Aapke bank se successful transaction huwa hai."),
    ("ST",  "st_bank_dhyaan",     "Bank transfer ho chuka hai sir, dhyaan dijiye."),
    ("ST",  "st_check_karti",     "Total outstanding amount check kar rahi hoon."),
    ("ST",  "st_account_12500",   "Sir, aapke account par 12,500 rupees ka outstanding hai."),

    # ---- Names (Indian financial context) ----
    ("NM",  "nm_rajat_fin",       "Main Rajat Finance ki taraf se call kar rahi hoon."),
    ("NM",  "nm_customer_ravi",   "Sir Ravi Kumar ji, aapka call transfer kar rahi hoon."),
    ("NM",  "nm_priya_customer",  "Priya madam, aapka payment received ho gaya hai."),

    # ---- Dates ----
    ("DT",  "dt_5_tareek",        "Aapki EMI 5 tareek ko due hai har mahine."),
    ("DT",  "dt_15_july",         "15 July tak payment karna hoga sir."),
    ("DT",  "dt_year_2025",       "2025 mein loan close hone wala hai aapka."),

    # ---- Financial acronyms ----
    ("FIN", "fin_kyc",            "Aapki KYC verify ho gayi hai sir."),
    ("FIN", "fin_pan",            "PAN card details submit kar dijiye."),
    ("FIN", "fin_aadhaar",        "Aadhaar linked hai aapke account se."),
    ("FIN", "fin_cibil",          "CIBIL score check karna zaroori hai."),

    # ---- Minimal pairs ----
    ("MP",  "mp_out_short",       "Total outstanding hai as of aaj."),
    ("MP",  "mp_out_long",        "Total outstanding amount check kar rahi hoon."),
    ("MP",  "mp_bank_pay",        "Aapke bank se successful transaction huwa hai."),
    ("MP",  "mp_pay_singular",    "Aapka payment complete ho gaya hai."),
    ("MP",  "mp_pay_plural",      "Aapke payments complete ho gaye hain."),
]

# UPI-containing texts get an additional UPI-normalized variant (V2+UPI overlay)
UPI_NORMALIZED_MAP = {
    "kd_upi_pay":      "Sir kya aap yoo pee aaye se payment karna prefer karenge?",
    "acr_upi_1":       "Sir, yoo pee aaye ka use karke payment karein.",
    "acr_upi_2":       "Aap yoo pee aaye ke through paise bhej sakte hain.",
    "acr_upi_3":       "Yoo pee aaye transaction fail ho gaya kya sir?",
    "acr_upi_4":       "Sir, yoo pee aaye PIN sahi hai na?",
    "acr_upi_5":       "Aapka yoo pee aaye ID kya hai?",
    "mix_upi_success": "आपका yoo pee aaye payment successful रहा।",
}

# V2+Devanagari diagnostic — for known number-heavy/problematic Hinglish keys we
# also try a full-Devanagari rendering (only under V2 wrapper). Not proposed for
# production; diagnostic only.
DEVA_DIAGNOSTIC_MAP = {
    "kd_outstanding_24k": "कुल बकाया चौबीस हज़ार पाँच सौ अड़सठ रुपये है आज तक।",
    "kd_num_9m":          "अठानवे लाख छिहत्तर हज़ार पाँच सौ तैंतालीस रुपये कुल बकाया है।",
    "num_amount_87k":     "कुल सत्तासी हज़ार छह सौ पचास रुपये का बकाया बाकी है।",
    "num_seven_digit":    "एक करोड़ पच्चीस लाख रुपये का loan amount finalize हो गया।",
    "kd_dhanyavaad_pay":  "धन्यवाद, आपका भुगतान सफल हो गया है।",
    "pay_outstanding_1l": "आपका बकाया एक लाख रुपये है।",
    "kd_principal_15k":   "मूल राशि पंद्रह हज़ार रुपये बाकी है।",
    "num_amount_50k":     "आपका amount पचास हज़ार रुपये है monthly।",
}
