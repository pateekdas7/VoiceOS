"""AdaptiveProsodyEngine — maps EmpathyConfig to VoiceConfig.

The EmpathyPlanner (Sprint-011) produces an EmpathyConfig describing the
agent's required tone, pacing, and language register.  The Adaptive Prosody
Engine translates this into concrete acoustic parameters (VoiceConfig) that
the TTS adapter can apply directly.

Mapping rules (V1 Ch20):

  Tone + Pacing combination → pitch_shift, rate_scale, pause_ms_after_clause:

  | Tone        | Pacing | pitch_shift | rate_scale | pause_ms |
  |-------------|--------|-------------|------------|----------|
  | EMPATHETIC  | SLOW   | -0.5        | 0.85       | 350      |  (HIGH_DISTRESS)
  | FIRM        | SLOW   | -0.3        | 0.90       | 250      |  (ANGRY/RESISTANT)
  | NEUTRAL     | NORMAL | 0.0         | 1.0        | 150      |  (NEUTRAL)
  | FRIENDLY    | FAST   | +0.1        | 1.05       | 100      |  (ENGAGED)
  | REASSURING  | SLOW   | -0.2        | 0.88       | 300      |
  | FORMAL      | NORMAL | 0.0         | 0.95       | 200      |
  | FRIENDLY    | NORMAL | +0.05       | 1.0        | 130      |
  | FRIENDLY    | SLOW   | 0.0         | 0.92       | 200      |

  When tone+pacing is not in the explicit table, the engine applies
  pacing-derived rate_scale first, then tone-derived pitch_shift and pause.

Language modifiers (applied after base mapping):
  - 'hi' / 'hi-IN' → rate_scale *= 0.97 (slight reduction for Hindi phonetics)
  - 'en' / 'en-IN' → no adjustment (baseline)
  - 'hi-en' (Hinglish) → rate_scale *= 0.98 (code-switch safe)

Architecture: V1 Ch20 (Adaptive Prosody Engine); V2 Ch14 (EmpathyPlanner).
"""

from __future__ import annotations

from src.libs.contracts.streaming import (
    EmpathyConfig,
    Pacing,
    Tone,
    VoiceConfig,
)

# ---------------------------------------------------------------------------
# Lookup table: (Tone, Pacing) → (pitch_shift, rate_scale, pause_ms)
# ---------------------------------------------------------------------------

_PROSODY_TABLE: dict[tuple[Tone, Pacing], tuple[float, float, int]] = {
    # HIGH_DISTRESS mapping — empathetic, slow
    (Tone.EMPATHETIC, Pacing.SLOW): (-0.5, 0.85, 350),
    # ANGRY/RESISTANT — firm, measured
    (Tone.FIRM, Pacing.SLOW): (-0.3, 0.90, 250),
    # NEUTRAL baseline
    (Tone.NEUTRAL, Pacing.NORMAL): (0.0, 1.0, 150),
    # ENGAGED — friendly, slightly faster
    (Tone.FRIENDLY, Pacing.FAST): (0.1, 1.05, 100),
    # REASSURING — slower than neutral, warm pitch
    (Tone.REASSURING, Pacing.SLOW): (-0.2, 0.88, 300),
    # FORMAL — measured, baseline pitch
    (Tone.FORMAL, Pacing.NORMAL): (0.0, 0.95, 200),
    # Friendly at normal pace
    (Tone.FRIENDLY, Pacing.NORMAL): (0.05, 1.0, 130),
    # Friendly but slower (de-escalation)
    (Tone.FRIENDLY, Pacing.SLOW): (0.0, 0.92, 200),
    # Neutral but fast (information-delivery)
    (Tone.NEUTRAL, Pacing.FAST): (0.0, 1.08, 120),
    # Firm at normal pace (correcting misconception)
    (Tone.FIRM, Pacing.NORMAL): (-0.1, 1.0, 175),
}

# Fallback rate_scale by Pacing when no exact table match
_PACING_RATE: dict[Pacing, float] = {
    Pacing.SLOW: 0.90,
    Pacing.NORMAL: 1.0,
    Pacing.FAST: 1.05,
}

# Fallback pitch_shift by Tone when no exact table match
_TONE_PITCH: dict[Tone, float] = {
    Tone.EMPATHETIC: -0.3,
    Tone.FIRM: -0.2,
    Tone.NEUTRAL: 0.0,
    Tone.FRIENDLY: 0.05,
    Tone.REASSURING: -0.2,
    Tone.FORMAL: 0.0,
}

# Fallback pause_ms by Pacing when no exact table match
_PACING_PAUSE: dict[Pacing, int] = {
    Pacing.SLOW: 300,
    Pacing.NORMAL: 150,
    Pacing.FAST: 100,
}

# Language rate modifiers
_LANGUAGE_RATE_MODIFIER: dict[str, float] = {
    "hi": 0.97,
    "hi-IN": 0.97,
    "hi-en": 0.98,
    "en": 1.0,
    "en-IN": 1.0,
}


class AdaptiveProsodyEngine:
    """Maps EmpathyConfig → VoiceConfig using V1 Ch20 prosody rules.

    This engine is the mandatory bridge between the EmpathyPlanner (Sprint-011)
    and the TTS layer.  The VoiceConfig it produces is passed directly to
    VeenaAdapter.synthesize_stream() without modification.

    Architecture: V1 Ch20.
    """

    def translate(
        self,
        empathy_config: EmpathyConfig,
        language: str = "hi-IN",
        turn_index: int = 0,
    ) -> VoiceConfig:
        """Translate an EmpathyConfig into a VoiceConfig.

        Args:
            empathy_config: Tone, pacing, and language register from
                            EmpathyPlanner.
            language:       BCP-47 language tag for the response.
                            Drives language-specific rate modifiers.
            turn_index:     Zero-based turn counter within the call.
                            Reserved for future per-turn adaptation.

        Returns:
            VoiceConfig with acoustic parameters ready for TTS.
        """
        key = (empathy_config.tone, empathy_config.pacing)

        if key in _PROSODY_TABLE:
            pitch_shift, rate_scale, pause_ms = _PROSODY_TABLE[key]
        else:
            pitch_shift = _TONE_PITCH.get(empathy_config.tone, 0.0)
            rate_scale = _PACING_RATE.get(empathy_config.pacing, 1.0)
            pause_ms = _PACING_PAUSE.get(empathy_config.pacing, 150)

        # Apply language rate modifier
        lang_key = language.split("-")[0] if "-" in language else language
        modifier = _LANGUAGE_RATE_MODIFIER.get(
            language,
            _LANGUAGE_RATE_MODIFIER.get(lang_key, 1.0),
        )
        rate_scale = round(rate_scale * modifier, 4)

        return VoiceConfig(
            pitch_shift=pitch_shift,
            rate_scale=rate_scale,
            energy_scale=1.0,
            pause_ms_after_clause=pause_ms,
            language=language,
        )
