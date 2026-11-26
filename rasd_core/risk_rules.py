"""Model 2, rule-based only: the loss formula that turns the three hazard scores, the adaptation measure and
the asset type into a RISK SCORE (the share of the asset's value expected to be lost).
Money at risk = asset value x risk score. Every number below is an editable assumption, not a regulator-approved value.
No training data and no machine learning are used here."""
from __future__ import annotations

# share of asset value lost at a hazard score of 1
MAX_FLOOD, MAX_HEAT, MAX_QUAKE = 0.20, 0.03, 0.10
FLOOD_EXPONENT, HEAT_EXPONENT, QUAKE_EXPONENT = 2.5, 2.0, 2.0
# a fully completed adaptation measure cuts the loss by this much
ADAPTATION_CUTS_FLOOD, ADAPTATION_CUTS_HEAT = 0.50, 0.30
# how much each building type changes the loss
FLOOD_FACTOR = {"concrete": 0.7, "reinforced concrete": 0.7, "masonry": 0.8, "steel": 1.0,
                "prefab": 1.3, "timber": 1.3, "tent": 1.5}
QUAKE_FACTOR = {"concrete": 1.0, "reinforced concrete": 0.8, "masonry": 1.3, "steel": 0.7,
                "prefab": 0.9, "timber": 0.8, "tent": 1.0}
MAX_SHARE = 0.8
BAND_CUTOFFS = {"medium": 0.03, "high": 0.08}      # share of asset value: Low < 3% <= Medium < 8% <= High

# which hazards a policy pays for (the "perils_covered" column of an asset file)
PERIL_SETS = {"all": ({"flood", "heat", "quake"}, 0.45), "flood": ({"flood"}, 0.20), "flood+heat": ({"flood", "heat"}, 0.15),
              "flood+quake": ({"flood", "quake"}, 0.10), "quake": ({"quake"}, 0.10)}


def loss_share(flood: float, heat: float, quake: float, adaptation: float, asset_type: str | None) -> float:
    """The loss formula. Scores are 0-1, adaptation is 0 (none), 0.5 (partly) or 1 (completed)."""
    text = (asset_type or "").strip().lower()
    key = next((k for k in sorted(FLOOD_FACTOR, key=len, reverse=True) if k in text), None)
    ff, qf = FLOOD_FACTOR.get(key, 1.0), QUAKE_FACTOR.get(key, 1.0)
    share = (ff * MAX_FLOOD * flood ** FLOOD_EXPONENT * (1 - ADAPTATION_CUTS_FLOOD * adaptation)
             + ff * MAX_HEAT * heat ** HEAT_EXPONENT * (1 - ADAPTATION_CUTS_HEAT * adaptation)
             + qf * MAX_QUAKE * quake ** QUAKE_EXPONENT)
    return float(min(MAX_SHARE, max(0.0, share)))


def insurance_payout_share(gross_share, cover_share, perils, flood, heat, quake, deductible_share=0.0):
    """THE INSURANCE RULE, the same one assess.py applies in money terms: the policy pays the part of the loss that
    comes from the hazards it covers, minus the deductible, but never more than the sum insured (all shares of value)."""
    total = flood + heat + quake
    covered_part = 1.0 if total <= 0 else sum(v for k, v in (("flood", flood), ("heat", heat), ("quake", quake)) if k in perils) / total
    return min(cover_share, max(0.0, gross_share * covered_part - deductible_share))
